// Experimental, same-process private computation runtime. See DESIGN.md.
#include <algorithm>
#include <cerrno>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sys/stat.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>
#include <unistd.h>

namespace {
constexpr size_t MAX_BYTES = 4 * 1024 * 1024;
constexpr size_t MAX_ITEMS = 100000;
constexpr int MAX_DEPTH = 64;
struct Error { const char* code; };
[[noreturn]] void fail(const char* code) { throw Error{code}; }
struct Bytes { std::string data; };
struct V {
    using List = std::vector<V>;
    using Map = std::map<std::string, V>;
    using Data = std::variant<std::nullptr_t, bool, int64_t, double, std::string,
                              Bytes, std::shared_ptr<List>, std::shared_ptr<Map>>;
    Data d;
    size_t nodes=1;
    int depth=0;
    V() : d(nullptr) {}
    V(bool x) : d(x) {}
    V(int64_t x) : d(x) {}
    V(double x) : d(x) { if (!std::isfinite(x)) fail("NumericError"); }
    // IR/wire text can expand a private value (hexadecimal bytes, escaped JSON).
    V(std::string x) : d(std::move(x)) { if (str().size() > MAX_BYTES * 8) fail("ResourceLimit"); }
    V(const char* x) : V(std::string(x)) {}
    V(Bytes x) : d(std::move(x)) {
        if (std::get<Bytes>(d).data.size() > MAX_BYTES) fail("ResourceLimit");
    }
    V(List x) : d(std::make_shared<List>(std::move(x))) {
        if (list().size() > MAX_ITEMS) fail("ResourceLimit");
        for(const auto& value:list()) {
            nodes+=value.nodes; depth=std::max(depth,value.depth+1);
            if(nodes>MAX_ITEMS*8||depth>MAX_DEPTH) fail("ResourceLimit");
        }
    }
    V(Map x) : d(std::make_shared<Map>(std::move(x))) {
        if (map().size() > MAX_ITEMS) fail("ResourceLimit");
        for(const auto& entry:map()) {
            nodes+=entry.second.nodes; depth=std::max(depth,entry.second.depth+1);
            if(nodes>MAX_ITEMS*8||depth>MAX_DEPTH) fail("ResourceLimit");
        }
    }
    template<class T> bool is() const { return std::holds_alternative<T>(d); }
    bool is_list() const { return is<std::shared_ptr<List>>(); }
    bool is_map() const { return is<std::shared_ptr<Map>>(); }
    const std::string& str() const {
        if (!is<std::string>()) fail("TypeError");
        return std::get<std::string>(d);
    }
    int64_t integer() const {
        if (!is<int64_t>()) fail("TypeError");
        return std::get<int64_t>(d);
    }
    const List& list() const {
        if (!is_list()) fail("TypeError");
        return *std::get<std::shared_ptr<List>>(d);
    }
    const Map& map() const {
        if (!is_map()) fail("TypeError");
        return *std::get<std::shared_ptr<Map>>(d);
    }
};
const V& field(const V& v, const std::string& key) {
    const auto& m = v.map();
    auto it = m.find(key);
    if (it == m.end()) fail("InvalidProgram");
    return it->second;
}
void append_utf8(std::string& out, uint32_t c) {
    if (c <= 0x7f) out += char(c);
    else if (c <= 0x7ff) {
        out += char(0xc0 | (c >> 6)); out += char(0x80 | (c & 63));
    } else if (c <= 0xffff && !(c >= 0xd800 && c <= 0xdfff)) {
        out += char(0xe0 | (c >> 12)); out += char(0x80 | ((c >> 6) & 63));
        out += char(0x80 | (c & 63));
    } else if (c >= 0x10000 && c <= 0x10ffff) {
        out += char(0xf0 | (c >> 18)); out += char(0x80 | ((c >> 12) & 63));
        out += char(0x80 | ((c >> 6) & 63)); out += char(0x80 | (c & 63));
    } else fail("InvalidData");
}
std::vector<std::string> characters(const std::string& s,bool collect=true,size_t* count=nullptr) {
    std::vector<std::string> out;
    size_t total=0;
    for (size_t i = 0; i < s.size();) {
        size_t start = i;
        unsigned char b = static_cast<unsigned char>(s[i++]);
        int n = b < 128 ? 0 : (b >= 0xc2 && b <= 0xdf ? 1 :
                (b >= 0xe0 && b <= 0xef ? 2 : (b >= 0xf0 && b <= 0xf4 ? 3 : -1)));
        if (n < 0 || i + n > s.size()) fail("InvalidData");
        uint32_t cp = n == 0 ? b : b & ((1 << (6 - n)) - 1);
        for (int j = 0; j < n; ++j) {
            unsigned char t = static_cast<unsigned char>(s[i++]);
            if ((t & 0xc0) != 0x80) fail("InvalidData");
            cp = (cp << 6) | (t & 63);
        }
        if ((n == 1 && cp < 128) || (n == 2 && cp < 2048) ||
            (n == 3 && cp < 65536) || cp > 0x10ffff ||
            (cp >= 0xd800 && cp <= 0xdfff)) fail("InvalidData");
        if(collect) out.push_back(s.substr(start, i - start));
        ++total;
    }
    if(count) *count=total;
    return out;
}
double parse_float(const std::string& s) {
    // strtod can report ERANGE for representable subnormals. Finite results are valid.
    char* end=nullptr;
    double result=std::strtod(s.c_str(),&end);
    if(end==s.c_str()||end!=s.c_str()+s.size()||!std::isfinite(result)) fail("NumericError");
    return result;
}

class Json {
    const std::string& s;
    size_t p = 0;
    void ws() { while (p < s.size() && (s[p]==' ' || s[p]=='\n' || s[p]=='\r' || s[p]=='\t')) ++p; }
    char take() { if (p >= s.size()) fail("InvalidData"); return s[p++]; }
    uint32_t hex4() {
        uint32_t x = 0;
        for (int i=0; i<4; ++i) {
            char c = take(); x <<= 4;
            if (c >= '0' && c <= '9') x += c-'0';
            else if (c >= 'a' && c <= 'f') x += c-'a'+10;
            else if (c >= 'A' && c <= 'F') x += c-'A'+10;
            else fail("InvalidData");
        }
        return x;
    }
    std::string string() {
        if (take() != '"') fail("InvalidData");
        std::string out;
        while (true) {
            char c = take();
            if (c == '"') break;
            if (static_cast<unsigned char>(c) < 32) fail("InvalidData");
            if (c != '\\') { out += c; continue; }
            switch (take()) {
                case '"': out += '"'; break; case '\\': out += '\\'; break;
                case '/': out += '/'; break; case 'b': out += '\b'; break;
                case 'f': out += '\f'; break; case 'n': out += '\n'; break;
                case 'r': out += '\r'; break; case 't': out += '\t'; break;
                case 'u': {
                    uint32_t x = hex4();
                    if (x >= 0xd800 && x <= 0xdbff) {
                        if (take() != '\\' || take() != 'u') fail("InvalidData");
                        uint32_t y = hex4();
                        if (y < 0xdc00 || y > 0xdfff) fail("InvalidData");
                        x = 0x10000 + ((x-0xd800)<<10) + y-0xdc00;
                    }
                    append_utf8(out, x); break;
                }
                default: fail("InvalidData");
            }
        }
        characters(out,false);
        return out;
    }
    V value(int depth) {
        if (depth > MAX_DEPTH) fail("ResourceLimit");
        ws(); if (p >= s.size()) fail("InvalidData");
        if (s[p] == '"') return V(string());
        if (s[p] == '[') {
            ++p; ws(); V::List out;
            if (p < s.size() && s[p] == ']') { ++p; return V(out); }
            while (true) {
                if (out.size() >= MAX_ITEMS) fail("ResourceLimit");
                out.push_back(value(depth+1)); ws();
                char c = take(); if (c == ']') break;
                if (c != ',') fail("InvalidData");
            }
            return V(out);
        }
        if (s[p] == '{') {
            ++p; ws(); V::Map out;
            if (p < s.size() && s[p] == '}') { ++p; return V(out); }
            while (true) {
                ws(); auto key = string(); ws();
                if (take() != ':' || out.count(key)) fail("InvalidData");
                if (out.size() >= MAX_ITEMS) fail("ResourceLimit");
                out.emplace(key, value(depth+1)); ws();
                char c = take(); if (c == '}') break;
                if (c != ',') fail("InvalidData");
            }
            return V(out);
        }
        for (const auto& literal : {std::string("true"), std::string("false"), std::string("null")}) {
            if (s.compare(p,literal.size(),literal)==0) {
                p += literal.size();
                if (literal=="null") return V();
                return V(literal=="true");
            }
        }
        size_t start = p;
        if (s[p]=='-') ++p;
        if (p >= s.size()) fail("InvalidData");
        if (s[p]=='0') ++p;
        else {
            if (s[p]<'1' || s[p]>'9') fail("InvalidData");
            while (p<s.size() && s[p]>='0' && s[p]<='9') ++p;
        }
        bool floating = false;
        if (p<s.size() && s[p]=='.') {
            floating=true; ++p; size_t begin=p;
            while (p<s.size() && s[p]>='0' && s[p]<='9') ++p;
            if (p==begin) fail("InvalidData");
        }
        if (p<s.size() && (s[p]=='e' || s[p]=='E')) {
            floating=true; ++p;
            if (p<s.size() && (s[p]=='+' || s[p]=='-')) ++p;
            size_t begin=p;
            while (p<s.size() && s[p]>='0' && s[p]<='9') ++p;
            if (p==begin) fail("InvalidData");
        }
        auto token = s.substr(start,p-start);
        try {
            if (floating) return V(parse_float(token));
            return V(int64_t(std::stoll(token)));
        } catch (const std::exception&) { fail("NumericError"); }
    }
public:
    explicit Json(const std::string& text) : s(text) {
        if (s.size() > MAX_BYTES * 8) fail("ResourceLimit");
    }
    V parse() { auto v=value(0); ws(); if (p != s.size()) fail("InvalidData"); return v; }
};
void bounded(std::string& s) { if (s.size() > MAX_BYTES * 8) fail("ResourceLimit"); }
void quote(std::string& out, const std::string& s) {
    static const char* hex="0123456789abcdef";
    out += '"';
    for (unsigned char c : s) {
        if (c=='"' || c=='\\') { out+='\\'; out+=char(c); }
        else if (c<32) { out+="\\u00"; out+=hex[c>>4]; out+=hex[c&15]; }
        else out+=char(c);
        bounded(out);
    }
    out += '"';
}
void dump_into(std::string& out, const V& v, int depth=0) {
    if (depth>MAX_DEPTH) fail("ResourceLimit");
    if (v.is<std::nullptr_t>()) out+="null";
    else if (v.is<bool>()) out += std::get<bool>(v.d) ? "true" : "false";
    else if (v.is<int64_t>()) out+=std::to_string(v.integer());
    else if (v.is<double>()) {
        std::ostringstream s; s.precision(17); s << std::get<double>(v.d); out+=s.str();
    } else if (v.is<std::string>()) quote(out,v.str());
    else if (v.is_list()) {
        out+='['; bool first=true;
        for (const auto& x:v.list()) {
            if (!first) out+=','; first=false; dump_into(out,x,depth+1);
        } out+=']';
    } else if (v.is_map()) {
        out+='{'; bool first=true;
        for (const auto& x:v.map()) {
            if (!first) out+=','; first=false; quote(out,x.first); out+=':';
            dump_into(out,x.second,depth+1);
        } out+='}';
    } else fail("TypeError");
    bounded(out);
}
std::string dump(const V& v) { std::string out; dump_into(out,v); return out; }
std::string hex_encode(const std::string& s) {
    const char* h="0123456789abcdef"; std::string out; out.reserve(s.size()*2);
    for (unsigned char b:s) { out+=h[b>>4]; out+=h[b&15]; }
    return out;
}
std::string hex_decode(const std::string& s) {
    if (s.size()%2 || s.size()>MAX_BYTES*2) fail("InvalidData");
    auto digit=[](char c)->int {
        if (c>='0'&&c<='9') return c-'0';
        if (c>='a'&&c<='f') return c-'a'+10;
        fail("InvalidData");
    };
    std::string out;
    for (size_t i=0;i<s.size();i+=2) out+=char((digit(s[i])<<4)|digit(s[i+1]));
    return out;
}
V from_wire(const V& wire, int depth=0) {
    if (depth>MAX_DEPTH) fail("ResourceLimit");
    const auto& a=wire.list(); if (a.empty()) fail("InvalidData");
    auto k=a[0].str();
    if (k=="null" && a.size()==1) return V();
    if (a.size()!=2) fail("InvalidData");
    if (k=="bool" && a[1].is<bool>()) return a[1];
    if (k=="int") {
        try { size_t n=0; auto x=std::stoll(a[1].str(),&n);
            if (n!=a[1].str().size()) fail("InvalidData"); return V(int64_t(x));
        } catch(const std::exception&) { fail("NumericError"); }
    }
    if (k=="float") {
        return V(parse_float(a[1].str()));
    }
    if (k=="str") { if(a[1].str().size()>MAX_BYTES) fail("ResourceLimit"); return a[1]; }
    if (k=="bytes") return V(Bytes{hex_decode(a[1].str())});
    if (k=="list") {
        V::List out; for(const auto& x:a[1].list()) out.push_back(from_wire(x,depth+1));
        return V(out);
    }
    if (k=="dict") {
        V::Map out; for(const auto& x:a[1].list()) {
            const auto& pair=x.list(); if(pair.size()!=2) fail("InvalidData");
            if(!out.emplace(pair[0].str(),from_wire(pair[1],depth+1)).second) fail("InvalidData");
        } return V(out);
    }
    fail("InvalidData");
}
V to_wire(const V& v, int depth=0) {
    if (depth>MAX_DEPTH) fail("ResourceLimit");
    if(v.is<std::nullptr_t>()) return V(V::List{V("null")});
    if(v.is<bool>()) return V(V::List{V("bool"),v});
    if(v.is<int64_t>()) return V(V::List{V("int"),V(std::to_string(v.integer()))});
    if(v.is<double>()) return V(V::List{V("float"),V(dump(v))});
    if(v.is<std::string>()) return V(V::List{V("str"),v});
    if(v.is<Bytes>()) return V(V::List{V("bytes"),V(hex_encode(std::get<Bytes>(v.d).data))});
    V::List out;
    if(v.is_list()) {
        for(const auto& x:v.list()) out.push_back(to_wire(x,depth+1));
        return V(V::List{V("list"),V(out)});
    }
    for(const auto& x:v.map()) out.push_back(V(V::List{V(x.first),to_wire(x.second,depth+1)}));
    return V(V::List{V("dict"),V(out)});
}

bool numeric(const V& x) { return x.is<int64_t>() || x.is<double>() || x.is<bool>(); }
int64_t as_int(const V& x) {
    if(x.is<bool>()) return std::get<bool>(x.d) ? 1 : 0;
    return x.integer();
}
double as_double(const V& x) {
    if(x.is<double>()) return std::get<double>(x.d);
    return double(as_int(x));
}
bool truth(const V& x) {
    if(x.is<std::nullptr_t>()) return false;
    if(x.is<bool>()) return std::get<bool>(x.d);
    if(x.is<int64_t>()) return x.integer()!=0;
    if(x.is<double>()) return std::get<double>(x.d)!=0;
    if(x.is<std::string>()) return !x.str().empty();
    if(x.is<Bytes>()) return !std::get<Bytes>(x.d).data.empty();
    if(x.is_list()) return !x.list().empty();
    return !x.map().empty();
}
bool equal(const V& a,const V& b,int depth=0) {
    if(depth>MAX_DEPTH) fail("ResourceLimit");
    if(numeric(a)&&numeric(b)) {
        if(!a.is<double>()&&!b.is<double>()) return as_int(a)==as_int(b);
        // Values are bounded; reject ambiguous mixed comparisons beyond exact double integers.
        if(a.is<int64_t>() && (a.integer()>9007199254740992LL || a.integer()<-9007199254740992LL)) fail("NumericError");
        if(b.is<int64_t>() && (b.integer()>9007199254740992LL || b.integer()<-9007199254740992LL)) fail("NumericError");
        return as_double(a)==as_double(b);
    }
    if(a.d.index()!=b.d.index()) return false;
    if(a.is<std::nullptr_t>()) return true;
    if(a.is<std::string>()) return a.str()==b.str();
    if(a.is<Bytes>()) return std::get<Bytes>(a.d).data==std::get<Bytes>(b.d).data;
    if(a.is_list()) {
        if(a.list().size()!=b.list().size()) return false;
        for(size_t i=0;i<a.list().size();++i) if(!equal(a.list()[i],b.list()[i],depth+1)) return false;
        return true;
    }
    if(a.map().size()!=b.map().size()) return false;
    for(const auto& x:a.map()) {
        auto it=b.map().find(x.first);
        if(it==b.map().end() || !equal(x.second,it->second,depth+1)) return false;
    }
    return true;
}
bool less(const V& a,const V& b) {
    if(numeric(a)&&numeric(b)) {
        if(!a.is<double>()&&!b.is<double>()) return as_int(a)<as_int(b);
        if(a.is<int64_t>() && (a.integer()>9007199254740992LL || a.integer()<-9007199254740992LL)) fail("NumericError");
        if(b.is<int64_t>() && (b.integer()>9007199254740992LL || b.integer()<-9007199254740992LL)) fail("NumericError");
        return as_double(a)<as_double(b);
    }
    if(a.is<std::string>()&&b.is<std::string>()) return a.str()<b.str();
    fail("TypeError");
}
V binary(const std::string& op,const V& a,const V& b) {
    if(op=="Add" && a.is<std::string>() && b.is<std::string>()) {
        if(a.str().size()+b.str().size()>MAX_BYTES) fail("ResourceLimit");
        return V(a.str()+b.str());
    }
    if(op=="Add" && a.is<Bytes>() && b.is<Bytes>()) {
        const auto& x=std::get<Bytes>(a.d).data; const auto& y=std::get<Bytes>(b.d).data;
        if(x.size()+y.size()>MAX_BYTES) fail("ResourceLimit");
        return V(Bytes{x+y});
    }
    if(op=="Add" && a.is_list() && b.is_list()) {
        if(a.list().size()+b.list().size()>MAX_ITEMS) fail("ResourceLimit");
        auto out=a.list(); out.insert(out.end(),b.list().begin(),b.list().end()); return V(out);
    }
    if(!numeric(a)||!numeric(b)) fail("TypeError");
    if(!a.is<double>()&&!b.is<double>()&&op!="Div") {
        int64_t x=as_int(a),y=as_int(b),out=0;
        if(op=="Add") { if(__builtin_add_overflow(x,y,&out)) fail("NumericError"); return V(out); }
        if(op=="Sub") { if(__builtin_sub_overflow(x,y,&out)) fail("NumericError"); return V(out); }
        if(op=="Mult") { if(__builtin_mul_overflow(x,y,&out)) fail("NumericError"); return V(out); }
        if(op=="FloorDiv"||op=="Mod") {
            if(y==0) fail("NumericError");
            if(x==std::numeric_limits<int64_t>::min()&&y==-1) {
                if(op=="Mod") return V(int64_t(0)); fail("NumericError");
            }
            int64_t q=x/y,r=x%y;
            if(r!=0&&((r<0)!=(y<0))) { --q; r+=y; }
            return V(op=="Mod" ? r : q);
        }
        fail("InvalidProgram");
    }
    double x=as_double(a),y=as_double(b);
    if(op=="Add") return V(x+y);
    if(op=="Sub") return V(x-y);
    if(op=="Mult") return V(x*y);
    if(y==0) fail("NumericError");
    if(op=="Div") return V(x/y);
    // Deliberately support integer floor division/modulo only in this prototype.
    if(op=="FloorDiv"||op=="Mod") fail("TypeError");
    fail("InvalidProgram");
}
size_t index_of(const V& key,size_t size) {
    auto i=as_int(key);
    if(i<0) i+=int64_t(size);
    if(i<0||uint64_t(i)>=size) fail("IndexError");
    return size_t(i);
}
V subscript(const V& x,const V& key) {
    if(x.is_list()) return x.list()[index_of(key,x.list().size())];
    if(x.is_map()) {
        auto it=x.map().find(key.str()); if(it==x.map().end()) fail("KeyError"); return it->second;
    }
    if(x.is<std::string>()) {
        auto chars=characters(x.str()); return V(chars[index_of(key,chars.size())]);
    }
    if(x.is<Bytes>()) {
        const auto& s=std::get<Bytes>(x.d).data;
        return V(int64_t(static_cast<unsigned char>(s[index_of(key,s.size())])));
    }
    fail("TypeError");
}
bool contains(const V& container,const V& needle) {
    if(container.is_list()) {
        for(const auto& x:container.list()) if(equal(x,needle)) return true;
        return false;
    }
    if(container.is_map()) return container.map().count(needle.str())!=0;
    if(container.is<std::string>()) return container.str().find(needle.str())!=std::string::npos;
    fail("TypeError");
}
V::List iterable(const V& v) {
    if(v.is_list()) return v.list();
    V::List out;
    if(v.is_map()) for(const auto& x:v.map()) out.push_back(V(x.first));
    else if(v.is<std::string>()) for(const auto& x:characters(v.str())) out.push_back(V(x));
    else if(v.is<Bytes>()) for(unsigned char x:std::get<Bytes>(v.d).data) out.push_back(V(int64_t(x)));
    else fail("TypeError");
    if(out.size()>MAX_ITEMS) fail("ResourceLimit");
    return out;
}
std::string scalar_text(const V& x) {
    if(x.is<std::string>()) return x.str();
    if(x.is<std::nullptr_t>()) return "None";
    if(x.is<bool>()) return std::get<bool>(x.d) ? "True" : "False";
    if(x.is<int64_t>()) return std::to_string(x.integer());
    if(x.is<double>()) {
        char buffer[64];
        auto converted=std::to_chars(buffer,buffer+sizeof(buffer),std::get<double>(x.d),std::chars_format::scientific);
        if(converted.ec!=std::errc()) fail("NumericError");
        std::string s(buffer,converted.ptr);
        auto e=s.find_first_of("eE");
        if(e==std::string::npos) return s.find('.')==std::string::npos ? s+".0" : s;
        int exponent=std::stoi(s.substr(e+1));
        if(exponent>=-4&&exponent<16) {
            bool negative=s[0]=='-';
            std::string digits=s.substr(negative?1:0,e-(negative?1:0));
            digits.erase(std::remove(digits.begin(),digits.end(),'.'),digits.end());
            int position=1+exponent;
            std::string out=negative?"-":"";
            if(position<=0) out+="0."+std::string(size_t(-position),'0')+digits;
            else if(size_t(position)>=digits.size()) out+=digits+std::string(size_t(position)-digits.size(),'0')+".0";
            else out+=digits.substr(0,size_t(position))+"."+digits.substr(size_t(position));
            return out;
        }
        return s;
    }
    fail("TypeError");
}
std::string read_file(const std::string& path) {
    if(path.find('\0')!=std::string::npos) fail("InputError");
    int fd=open(path.c_str(),O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if(fd<0) fail("InputError");
    struct Guard { int fd; ~Guard(){close(fd);} } guard{fd};
    // Only regular files: avoid blocking on devices, FIFOs and unbounded sources.
    struct stat st{};
    if(fstat(fd,&st)!=0 || !S_ISREG(st.st_mode)) fail("InputError");
    if(st.st_size<0 || uint64_t(st.st_size)>MAX_BYTES) fail("ResourceLimit");
    std::string out; char buf[8192];
    while(true) {
        ssize_t n=read(fd,buf,sizeof(buf));
        if(n<0) { if(errno==EINTR) continue; fail("InputError"); }
        if(n==0) break;
        if(out.size()+size_t(n)>MAX_BYTES) fail("ResourceLimit");
        out.append(buf,size_t(n));
    }
    return out;
}
std::string csv_cell(const V& v) {
    auto s=v.is<std::nullptr_t>() ? std::string() : scalar_text(v);
    if(s.find_first_of(",\"\r\n")==std::string::npos) return s;
    std::string out="\"";
    for(char c:s) { if(c=='"') out+='"'; out+=c; }
    return out+'"';
}

struct Return { V value; };
struct Break {};
struct Continue {};
struct Region {
    std::unordered_map<std::string,V> programs;
    std::unordered_map<uint64_t,V> objects;
    uint64_t next_object=1;
    int64_t max_steps;
    explicit Region(int64_t limit):max_steps(limit){}
};
struct VM {
    Region& region;
    int64_t steps=0;
    int call_depth=0;
    using Env=std::map<std::string,V>;
    void tick() { if(++steps>region.max_steps) fail("ResourceLimit"); }
    V eval(const V& node,Env& env,int depth=0);
    void statements(const V& nodes,Env& env);
    void assign(const V& target,const V& value,Env& env);
    V call(const std::string& name,const V::List& args);
    V builtin(const std::string& name,const V::List& args);
    V method(const std::string& name,const V& receiver,const V::List& args);
};
void arity(const V::List& args,size_t minimum,size_t maximum) {
    if(args.size()<minimum||args.size()>maximum) fail("TypeError");
}
V VM::builtin(const std::string& name,const V::List& args) {
    tick();
    if(name=="read_json"||name=="read_text"||name=="read_bytes") {
        arity(args,1,1); auto raw=read_file(args[0].str());
        if(name=="read_bytes") return V(Bytes{raw});
        if(name=="read_json") return Json(raw).parse();
        characters(raw,false); return V(raw);
    }
    if(name=="json_bytes") { arity(args,1,1); return V(Bytes{dump(args[0])}); }
    if(name=="utf8_bytes") { arity(args,1,1); return V(Bytes{args[0].str()}); }
    if(name=="csv_bytes") {
        arity(args,2,2); const auto& rows=args[0].list(); const auto& columns=args[1].list();
        for(const auto& c:columns) c.str();
        std::string out;
        auto line=[&](const V::List& cells) {
            bool first=true;
            for(const auto& cell:cells) {
                if(!first) out+=','; first=false;
                auto encoded=csv_cell(cell);
                out+=(cells.size()==1&&encoded.empty()) ? "\"\"" : encoded;
            }
            out+="\r\n"; if(out.size()>MAX_BYTES) fail("ResourceLimit"); tick();
        };
        line(columns);
        for(const auto& row:rows) {
            V::List cells;
            for(const auto& c:columns) {
                auto it=row.map().find(c.str()); cells.push_back(it==row.map().end()?V():it->second);
            }
            line(cells);
        }
        return V(Bytes{out});
    }
    if(name=="len") {
        arity(args,1,1); const auto& x=args[0];
        if(x.is_list()) return V(int64_t(x.list().size()));
        if(x.is_map()) return V(int64_t(x.map().size()));
        if(x.is<std::string>()) { size_t count=0; characters(x.str(),false,&count); return V(int64_t(count)); }
        if(x.is<Bytes>()) return V(int64_t(std::get<Bytes>(x.d).data.size()));
        fail("TypeError");
    }
    if(name=="bool") { arity(args,1,1); return V(truth(args[0])); }
    if(name=="str") { arity(args,1,1); return V(scalar_text(args[0])); }
    if(name=="abs") {
        arity(args,1,1);
        if(!numeric(args[0])) fail("TypeError");
        if(args[0].is<double>()) return V(std::fabs(as_double(args[0])));
        auto x=as_int(args[0]); if(x==std::numeric_limits<int64_t>::min()) fail("NumericError");
        return V(x<0?-x:x);
    }
    if(name=="sum"||name=="min"||name=="max") {
        arity(args,1,name=="sum"?2:1); auto items=iterable(args[0]);
        if(name!="sum"&&items.empty()) fail("ValueError");
        V out=name=="sum" ? (args.size()==2 ? args[1] : V(int64_t(0))) : items[0];
        if(name=="sum"&&(out.is<std::string>()||out.is<Bytes>())) fail("TypeError");
        for(const auto& item:items) {
            tick();
            if(name=="sum") out=binary("Add",out,item);
            else if(name=="min" ? less(item,out) : less(out,item)) out=item;
        }
        return out;
    }
    if(name=="range") {
        arity(args,1,3); int64_t start=0,stop=as_int(args[0]),step=1;
        if(args.size()>1) { start=stop; stop=as_int(args[1]); }
        if(args.size()>2) step=as_int(args[2]);
        if(step==0) fail("ValueError");
        V::List out;
        for(int64_t i=start; step>0 ? i<stop : i>stop;) {
            tick(); if(out.size()>=MAX_ITEMS) fail("ResourceLimit");
            out.push_back(V(i));
            int64_t next; if(__builtin_add_overflow(i,step,&next)) break;
            i=next;
        }
        return V(out);
    }
    fail("UnsupportedOperation");
}
V VM::method(const std::string& name,const V& receiver,const V::List& args) {
    tick();
    if(name=="get") {
        arity(args,1,2); auto it=receiver.map().find(args[0].str());
        return it==receiver.map().end() ? (args.size()==2?args[1]:V()) : it->second;
    }
    if(name=="keys"||name=="values"||name=="items") {
        arity(args,0,0); V::List out;
        for(const auto& x:receiver.map()) {
            tick();
            out.push_back(name=="keys" ? V(x.first) : (name=="values" ? x.second :
                V(V::List{V(x.first),x.second})));
        }
        return V(out);
    }
    if(name=="encode") {
        arity(args,0,1);
        if(!args.empty()&&args[0].str()!="utf-8"&&args[0].str()!="utf8") fail("UnsupportedOperation");
        return V(Bytes{receiver.str()});
    }
    fail("UnsupportedOperation");
}

V VM::eval(const V& node,Env& env,int depth) {
    tick(); if(depth>MAX_DEPTH) fail("ResourceLimit");
    auto kind=field(node,"k").str();
    if(kind=="literal") return from_wire(field(node,"v"));
    if(kind=="name") {
        auto it=env.find(field(node,"id").str()); if(it==env.end()) fail("NameError");
        return it->second;
    }
    if(kind=="list") {
        V::List out; for(const auto& x:field(node,"items").list()) out.push_back(eval(x,env,depth+1));
        return V(out);
    }
    if(kind=="dict") {
        V::Map out;
        for(const auto& item:field(node,"items").list()) {
            auto key=eval(item.list().at(0),env,depth+1).str();
            out[key]=eval(item.list().at(1),env,depth+1);
        }
        return V(out);
    }
    if(kind=="bin") {
        auto left=eval(field(node,"a"),env,depth+1);
        auto right=eval(field(node,"b"),env,depth+1);
        return binary(field(node,"op").str(),left,right);
    }
    if(kind=="unary") {
        auto x=eval(field(node,"v"),env,depth+1); auto op=field(node,"op").str();
        if(op=="Not") return V(!truth(x));
        if(!numeric(x)) fail("TypeError");
        if(op=="UAdd") return x.is<bool>()?V(as_int(x)):x;
        if(op=="USub") {
            // Subtracting from +0.0 loses the negative sign when x is +0.0.
            if(x.is<double>()) return V(-as_double(x));
            return binary("Sub",V(int64_t(0)),x);
        }
        fail("InvalidProgram");
    }
    if(kind=="bool") {
        const auto& nodes=field(node,"items").list(); if(nodes.empty()) fail("InvalidProgram");
        bool is_and=field(node,"op").str()=="And";
        V out;
        for(const auto& n:nodes) {
            out=eval(n,env,depth+1);
            if(is_and ? !truth(out) : truth(out)) return out;
        } return out;
    }
    if(kind=="compare") {
        auto left=eval(field(node,"a"),env,depth+1);
        const auto& ops=field(node,"ops").list(); const auto& right_nodes=field(node,"items").list();
        if(ops.size()!=right_nodes.size()) fail("InvalidProgram");
        for(size_t i=0;i<ops.size();++i) {
            auto right=eval(right_nodes[i],env,depth+1); auto op=ops[i].str(); bool result;
            if(op=="Eq") result=equal(left,right);
            else if(op=="NotEq") result=!equal(left,right);
            else if(op=="Lt") result=less(left,right);
            else if(op=="Gt") result=less(right,left);
            else if(op=="LtE") result=less(left,right)||equal(left,right);
            else if(op=="GtE") result=less(right,left)||equal(left,right);
            else if(op=="In") result=contains(right,left);
            else if(op=="NotIn") result=!contains(right,left);
            else if(op=="Is"||op=="IsNot") {
                if(!(left.is<std::nullptr_t>()||left.is<bool>()) ||
                   !(right.is<std::nullptr_t>()||right.is<bool>())) {
                    // Comparing a value to None is always supported.
                    if(!left.is<std::nullptr_t>()&&!right.is<std::nullptr_t>()) fail("TypeError");
                }
                result=left.d.index()==right.d.index() && equal(left,right);
                if(op=="IsNot") result=!result;
            } else fail("InvalidProgram");
            if(!result) return V(false);
            left=right;
        }
        return V(true);
    }
    if(kind=="ifexpr") return eval(field(node,truth(eval(field(node,"test"),env,depth+1))?"yes":"no"),env,depth+1);
    if(kind=="subscript") {
        auto container=eval(field(node,"v"),env,depth+1);
        auto key=eval(field(node,"key"),env,depth+1);
        return subscript(container,key);
    }
    if(kind=="call"||kind=="builtin"||kind=="method") {
        V receiver;
        if(kind=="method") receiver=eval(field(node,"receiver"),env,depth+1);
        V::List args; for(const auto& n:field(node,"args").list()) args.push_back(eval(n,env,depth+1));
        auto name=field(node,"name").str();
        if(kind=="call") return call(name,args);
        if(kind=="builtin") return builtin(name,args);
        return method(name,receiver,args);
    }
    if(kind=="listcomp") {
        auto values=iterable(eval(field(node,"iter"),env,depth+1)); V::List out; Env local=env;
        for(const auto& v:values) {
            tick(); assign(field(node,"target"),v,local); bool keep=true;
            for(const auto& test:field(node,"ifs").list()) if(!truth(eval(test,local,depth+1))) { keep=false; break; }
            if(keep) out.push_back(eval(field(node,"elt"),local,depth+1));
        }
        return V(out);
    }
    fail("InvalidProgram");
}
void VM::assign(const V& target,const V& value,Env& env) {
    tick(); auto kind=field(target,"k").str();
    if(kind=="name") { env[field(target,"id").str()]=value; return; }
    if(kind=="unpack") {
        const auto& targets=field(target,"items").list(); auto values=iterable(value);
        if(targets.size()!=values.size()) fail("ValueError");
        for(size_t i=0;i<targets.size();++i) assign(targets[i],values[i],env);
        return;
    }
    if(kind=="subscript") {
        // Collection updates use value semantics, never host-visible aliases.
        auto name=field(target,"id").str(); auto it=env.find(name);
        if(it==env.end()) fail("NameError");
        auto key=eval(field(target,"key"),env);
        if(it->second.is_map()) {
            auto out=it->second.map();
            if(!out.count(key.str())&&out.size()>=MAX_ITEMS) fail("ResourceLimit");
            out[key.str()]=value; it->second=V(out); return;
        }
        auto out=it->second.list(); out[index_of(key,out.size())]=value; it->second=V(out); return;
    }
    fail("InvalidProgram");
}
void VM::statements(const V& nodes,Env& env) {
    for(const auto& node:nodes.list()) {
        tick(); auto kind=field(node,"k").str();
        if(kind=="return") throw Return{eval(field(node,"v"),env)};
        if(kind=="assign") { auto v=eval(field(node,"v"),env); assign(field(node,"target"),v,env); }
        else if(kind=="expr") { eval(field(node,"v"),env); }
        else if(kind=="if") statements(field(node,truth(eval(field(node,"test"),env))?"yes":"no"),env);
        else if(kind=="for") {
            auto values=iterable(eval(field(node,"iter"),env)); bool broke=false;
            for(const auto& value:values) {
                tick(); assign(field(node,"target"),value,env);
                try { statements(field(node,"body"),env); }
                catch(const Continue&) { continue; }
                catch(const Break&) { broke=true; break; }
            }
            if(!broke) statements(field(node,"else"),env);
        } else if(kind=="while") {
            bool broke=false;
            while(truth(eval(field(node,"test"),env))) {
                tick();
                try { statements(field(node,"body"),env); }
                catch(const Continue&) { continue; }
                catch(const Break&) { broke=true; break; }
            }
            if(!broke) statements(field(node,"else"),env);
        } else if(kind=="break") throw Break{};
        else if(kind=="continue") throw Continue{};
        else if(kind!="pass") fail("InvalidProgram");
    }
}
V VM::call(const std::string& name,const V::List& args) {
    tick(); if(++call_depth>MAX_DEPTH) fail("ResourceLimit");
    struct Guard { int& depth; ~Guard(){--depth;} } guard{call_depth};
    auto it=region.programs.find(name); if(it==region.programs.end()) fail("UnknownFunction");
    const auto& parameters=field(it->second,"params").list();
    if(parameters.size()!=args.size()) fail("TypeError");
    Env env; for(size_t i=0;i<args.size();++i) env[parameters[i].str()]=args[i];
    try { statements(field(it->second,"body"),env); }
    catch(const Return& result) { return result.value; }
    catch(const Break&) { fail("InvalidProgram"); }
    catch(const Continue&) { fail("InvalidProgram"); }
    return V();
}

std::mutex gate;
std::unordered_map<uint64_t,std::unique_ptr<Region>> regions;
uint64_t next_region=1;
uint64_t next_temporary=1;
thread_local std::string last_error;
Region& get_region(uint64_t id) {
    auto it=regions.find(id); if(it==regions.end()) fail("ClosedRegion");
    return *it->second;
}
const V& get_object(Region& r,uint64_t id) {
    auto it=r.objects.find(id); if(it==r.objects.end()) fail("InvalidReference");
    return it->second;
}
template<class T,class F> T guard_api(T failure,F fn) {
    std::lock_guard<std::mutex> lock(gate); last_error.clear();
    try { return fn(); }
    catch(const Error& error) { last_error=error.code; }
    catch(const std::bad_alloc&) { last_error="ResourceLimit"; }
    catch(...) { last_error="InternalError"; }
    return failure;
}
struct FD {
    int value=-1;
    explicit FD(int fd=-1):value(fd){}
    ~FD(){ if(value>=0) close(value); }
    void reset(int fd) { if(value>=0) close(value); value=fd; }
};
void export_file(const std::string& path,const std::string& data) {
    if(path.empty()||path[0]!='/'||path.back()=='/'||path.find('\0')!=std::string::npos) fail("InvalidDestination");
    std::vector<std::string> parts; size_t p=1;
    while(p<path.size()) {
        auto end=path.find('/',p); if(end==std::string::npos) end=path.size();
        auto part=path.substr(p,end-p);
        if(part.empty()||part=="."||part=="..") fail("InvalidDestination");
        parts.push_back(part); p=end+1;
    }
    if(parts.empty()) fail("InvalidDestination");
    FD parent(open("/",O_RDONLY|O_DIRECTORY|O_CLOEXEC));
    if(parent.value<0) fail("OutputError");
    for(size_t i=0;i+1<parts.size();++i) {
        int next=openat(parent.value,parts[i].c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(next<0) fail("InvalidDestination"); parent.reset(next);
    }
    std::string temporary;
    FD file;
    for(int attempts=0;attempts<16;++attempts) {
        temporary=".privpy-"+std::to_string(getpid())+"-"+std::to_string(next_temporary++);
        int fd=openat(parent.value,temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        if(fd>=0) { file.reset(fd); break; }
        if(errno!=EEXIST) fail("OutputError");
    }
    if(file.value<0) fail("OutputError");
    struct Cleanup {
        int parent; const std::string& name;
        ~Cleanup(){ unlinkat(parent,name.c_str(),0); }
    } cleanup{parent.value,temporary};
    size_t offset=0;
    while(offset<data.size()) {
        ssize_t n=write(file.value,data.data()+offset,data.size()-offset);
        if(n<0 && errno==EINTR) continue;
        if(n<=0) fail("OutputError");
        offset+=size_t(n);
    }
    if(fsync(file.value)!=0) fail("OutputError");
    // Publish only complete output, without replacing an existing file or symlink.
    if(linkat(parent.value,temporary.c_str(),parent.value,parts.back().c_str(),0)!=0) {
        if(errno==EEXIST) fail("FileExists");
        fail("OutputError");
    }
}
} // namespace

extern "C" {
const char* pr_last_error() { return last_error.c_str(); }
uint64_t pr_create(int64_t max_steps) {
    return guard_api<uint64_t>(0,[&] {
        if(max_steps<1||max_steps>10000000) fail("InvalidConfiguration");
        uint64_t id=next_region++;
        regions.emplace(id,std::make_unique<Region>(max_steps)); return id;
    });
}
int pr_close(uint64_t id) {
    return guard_api<int>(0,[&] {
        get_region(id); regions.erase(id); return 1;
    });
}
int pr_register(uint64_t id,const char* name,const char* program) {
    return guard_api<int>(0,[&] {
        if(!name||!program) fail("InvalidProgram");
        auto& r=get_region(id); V parsed=Json(std::string(program)).parse();
        field(parsed,"params").list(); field(parsed,"body").list();
        if(r.programs.count(name)) fail("DuplicateFunction");
        r.programs.emplace(name,std::move(parsed)); return 1;
    });
}
uint64_t pr_call(uint64_t id,const char* name,const char* arguments) {
    return guard_api<uint64_t>(0,[&] {
        if(!name||!arguments) fail("InvalidProgram");
        auto& r=get_region(id); auto request=Json(std::string(arguments)).parse();
        V::List args;
        for(const auto& item:request.list()) {
            if(item.map().count("ref")) {
                auto ref=field(item,"ref").integer();
                if(ref<=0) fail("InvalidReference");
                args.push_back(get_object(r,uint64_t(ref)));
            } else args.push_back(from_wire(field(item,"value")));
        }
        VM vm{r}; auto result=vm.call(name,args);
        uint64_t handle=r.next_object++;
        r.objects.emplace(handle,std::move(result)); return handle;
    });
}
void* pr_export_value(uint64_t id,uint64_t handle) {
    return guard_api<void*>(nullptr,[&]() -> void* {
        auto& r=get_region(id); auto out=dump(to_wire(get_object(r,handle)));
        void* buffer=std::malloc(out.size()+1); if(!buffer) fail("ResourceLimit");
        std::memcpy(buffer,out.c_str(),out.size()+1); return buffer;
    });
}
void pr_free(void* pointer) { std::free(pointer); }
int pr_export_file(uint64_t id,uint64_t handle,const char* path) {
    return guard_api<int>(0,[&] {
        if(!path) fail("InvalidDestination");
        auto& r=get_region(id); const auto& value=get_object(r,handle);
        if(!value.is<Bytes>()) fail("ExportTypeError");
        export_file(std::string(path),std::get<Bytes>(value.d).data); return 1;
    });
}
}

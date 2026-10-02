// Local study prototype. Reuse the interpreter without copying its implementation.
// This executable owns one region and one inherited socket, with no public listener.
#include "runtime.cpp"
#include <csignal>
#include <sys/resource.h>
#include <sys/socket.h>
#if defined(__linux__)
#include <sys/prctl.h>
#elif defined(__APPLE__)
#include <sys/ptrace.h>
#endif

namespace {
constexpr size_t MAX_FRAME = 32 * 1024 * 1024;

bool read_exact(int fd, char* destination, size_t size, bool allow_eof=false) {
    size_t offset=0;
    while(offset<size) {
        ssize_t count=recv(fd,destination+offset,size-offset,0);
        if(count<0 && errno==EINTR) continue;
        if(count==0 && offset==0 && allow_eof) return false;
        if(count<=0) fail("ConnectionClosed");
        offset+=size_t(count);
    }
    return true;
}

bool read_frame(int fd, std::string& payload) {
    unsigned char header[4];
    if(!read_exact(fd,reinterpret_cast<char*>(header),4,true)) return false;
    uint32_t size=(uint32_t(header[0])<<24)|(uint32_t(header[1])<<16)
                 |(uint32_t(header[2])<<8)|uint32_t(header[3]);
    if(size==0 || size>MAX_FRAME) fail("InvalidFrame");
    payload.resize(size);
    return read_exact(fd,payload.data(),size);
}

void write_all(int fd, const char* data, size_t size) {
    size_t offset=0;
    while(offset<size) {
        ssize_t count=send(fd,data+offset,size-offset,0);
        if(count<0 && errno==EINTR) continue;
        if(count<=0) fail("ConnectionClosed");
        offset+=size_t(count);
    }
}

void write_frame(int fd, const V& response) {
    auto payload=dump(response);
    if(payload.size()>MAX_FRAME) fail("ResourceLimit");
    uint32_t size=uint32_t(payload.size());
    char header[4]={char(size>>24),char(size>>16),char(size>>8),char(size)};
    write_all(fd,header,4);
    write_all(fd,payload.data(),payload.size());
}

V startup_protections() {
    struct rlimit limit{0,0};
    if(setrlimit(RLIMIT_CORE,&limit)!=0) fail("WorkerSetup");
    V::Map report{{"protocol",V(int64_t(1))},{"pid",V(int64_t(getpid()))},
                  {"core_dumps_disabled",V(true)}};
#if defined(__linux__)
    if(prctl(PR_SET_DUMPABLE,0L,0L,0L,0L)!=0
       || prctl(PR_SET_NO_NEW_PRIVS,1L,0L,0L,0L)!=0) fail("WorkerSetup");
    report["inspection_restriction"]=V("linux-nondumpable");
    report["no_new_privileges"]=V(true);
#elif defined(__APPLE__)
    if(ptrace(PT_DENY_ATTACH,0,nullptr,0)!=0) fail("WorkerSetup");
    report["inspection_restriction"]=V("darwin-deny-attach");
    report["no_new_privileges"]=V(false);
#else
    fail("WorkerSetup");
#endif
    return V(report);
}

void fields(const V& request, std::initializer_list<const char*> names) {
    if(request.map().size()!=names.size()) fail("InvalidRequest");
    for(const auto* name:names) field(request,name);
}

const std::string& c_string(const V& request, const char* name) {
    const auto& value=field(request,name).str();
    if(value.find('\0')!=std::string::npos) fail("InvalidRequest");
    return value;
}

template<class T> T checked(T result) {
    if(!result) fail(pr_last_error());
    return result;
}

uint64_t reference(const V& request) {
    auto value=field(request,"handle").integer();
    if(value<=0) fail("InvalidReference");
    return uint64_t(value);
}

V dispatch(const V& request, uint64_t& region, bool& closed) {
    auto op=field(request,"op").str();
    if(op=="create") {
        fields(request,{"op","max_steps"});
        if(region!=0) fail("InvalidRequest");
        region=checked(pr_create(field(request,"max_steps").integer()));
        return V(int64_t(region));
    }
    if(region==0) fail("ClosedRegion");
    if(op=="register") {
        fields(request,{"op","name","program"});
        checked(pr_register(region,c_string(request,"name").c_str(),c_string(request,"program").c_str()));
        return V();
    }
    if(op=="call") {
        fields(request,{"op","name","arguments"});
        auto arguments=dump(field(request,"arguments"));
        return V(int64_t(checked(pr_call(region,c_string(request,"name").c_str(),arguments.c_str()))));
    }
    if(op=="export_value") {
        fields(request,{"op","handle"});
        void* data=checked(pr_export_value(region,reference(request)));
        struct Buffer { void* data; ~Buffer(){pr_free(data);} } buffer{data};
        std::string encoded(static_cast<const char*>(data));
        return Json(encoded).parse();
    }
    if(op=="export_file") {
        fields(request,{"op","handle","path"});
        // Destination rules remain enforced by the native file exporter.
        checked(pr_export_file(region,reference(request),c_string(request,"path").c_str()));
        return V();
    }
    if(op=="close") {
        fields(request,{"op"});
        checked(pr_close(region)); region=0; closed=true;
        return V();
    }
    fail("InvalidRequest");
}

V error_reply(const char* code) {
    return V(V::Map{{"ok",V(false)},{"error",V(code)}});
}
}

int main(int argc, char** argv) {
    if(argc!=3 || std::string(argv[1])!="--fd") return 2;
    char* end=nullptr;
    long parsed=std::strtol(argv[2],&end,10);
    if(*end!='\0' || parsed<0 || parsed>std::numeric_limits<int>::max()) return 2;
    FD connection{int(parsed)};
    std::signal(SIGPIPE,SIG_IGN);
    uint64_t region=0;
    try {
        // This handshake completes before the worker accepts programs or data.
        write_frame(connection.value,startup_protections());
        bool closed=false;
        while(!closed) {
            std::string payload;
            if(!read_frame(connection.value,payload)) break;
            V response;
            try {
                auto request=Json(payload).parse();
                response=V(V::Map{{"ok",V(true)},{"result",dispatch(request,region,closed)}});
            } catch(const Error& error) {
                response=error_reply(error.code);
            } catch(const std::bad_alloc&) {
                response=error_reply("ResourceLimit");
            } catch(...) {
                response=error_reply("InternalError");
            }
            write_frame(connection.value,response);
        }
    } catch(...) {
        // No values, source paths, or parser input are written to standard streams.
        if(region!=0) pr_close(region);
        return 1;
    }
    if(region!=0) pr_close(region);
    return 0;
}

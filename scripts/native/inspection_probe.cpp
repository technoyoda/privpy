// Test utility only. Inspects children it creates, never a user-supplied PID.
// Each invocation owns a fresh control fixture or the real protected worker.
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <string>
#include <stdexcept>
#include <fcntl.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#if defined(__APPLE__)
#include <mach/mach.h>
#include <mach/mach_vm.h>
#elif defined(__linux__)
#include <sys/uio.h>
#endif

namespace {
constexpr unsigned char MARKER = 0x5a;
volatile sig_atomic_t target_pid = -1;

void exact(int fd, void* data, size_t size, bool sending = false) {
    auto* bytes = static_cast<char*>(data);
    size_t offset = 0;
    while (offset < size) {
        ssize_t count = sending ? write(fd, bytes + offset, size - offset)
                                : read(fd, bytes + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("handshake failed");
        offset += size_t(count);
    }
}

void reap(pid_t pid) {
    if (pid <= 0) return;
    int status;
    pid_t state;
    do { state = waitpid(pid, &status, WNOHANG); } while (state < 0 && errno == EINTR);
    if (state != 0) return;
    kill(pid, SIGKILL);
    while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
}

// Darwin may signal the attaching parent when the child denies attachment.
// Installed only around ptrace; async-signal-safe cleanup keeps the target owned.
void attach_signal(int) {
    reap(pid_t(target_pid));
    constexpr char result[] = "{\"outcome\":\"attach_signalled\",\"code\":11}\n";
    ssize_t ignored = write(STDOUT_FILENO, result, sizeof(result) - 1);
    (void)ignored;
    _exit(0);
}

struct Target {
    pid_t pid = -1;
    int connection = -1;
    uintptr_t address = 0;
    ~Target() { if (connection >= 0) close(connection); reap(pid); }
    void start(const char* executable, bool control) {
        int pair[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair) != 0)
            throw std::runtime_error("socket failed");
        pid = fork();
        if (pid < 0) { close(pair[0]); close(pair[1]); throw std::runtime_error("fork failed"); }
        if (pid == 0) {
            close(pair[0]);
            int devnull = open("/dev/null", O_RDWR);
            if (devnull < 0) _exit(2);
            for (int fd = 0; fd < 3; ++fd) if (dup2(devnull, fd) < 0) _exit(2);
            if (devnull > 2) close(devnull);
            std::string fd = std::to_string(pair[1]);
            execl(executable, executable, control ? "--fixture" : "--fd", fd.c_str(), nullptr);
            _exit(2);
        }
        close(pair[1]);
        connection = pair[0];
        if (control) {
            exact(connection, &address, sizeof(address));
        } else {
            unsigned char header[4];
            exact(connection, header, sizeof(header));
            uint32_t size = (uint32_t(header[0]) << 24) | (uint32_t(header[1]) << 16)
                          | (uint32_t(header[2]) << 8) | uint32_t(header[3]);
            if (size == 0 || size > 4096) throw std::runtime_error("invalid ready frame");
            std::string ready(size, '\0');
            exact(connection, ready.data(), ready.size());
            if (ready.find("\"core_dumps_disabled\":true") == std::string::npos
                || ready.find("\"inspection_restriction\"") == std::string::npos)
                throw std::runtime_error("missing worker protections");
        }
    }
};

void report(const char* outcome, int code = 0) {
    std::printf("{\"outcome\":\"%s\",\"code\":%d}\n", outcome, code);
}

#if defined(__linux__)
void read_result(ssize_t count, unsigned char byte, bool control, int code) {
    if (count == 1) report(!control || byte == MARKER ? "read_succeeded" : "control_mismatch");
    else report(code == EPERM || code == EACCES ? "denied" : "error", code);
}
#endif

void trace(Target& target, bool exceptions) {
    target_pid = target.pid;
    struct sigaction handler{};
    handler.sa_handler = attach_signal;
    sigemptyset(&handler.sa_mask);
    if (sigaction(SIGSEGV, &handler, nullptr) != 0) throw std::runtime_error("signal setup failed");
    errno = 0;
#if defined(__APPLE__)
    // Numeric 10 is the legacy PT_ATTACH, deliberately compared with ATTACHEXC.
    int result = ptrace(exceptions ? PT_ATTACHEXC : 10, target.pid, nullptr, 0);
#else
    (void)exceptions;
    long result = ptrace(PTRACE_ATTACH, target.pid, nullptr, nullptr);
#endif
    int code = errno;
    std::signal(SIGSEGV, SIG_DFL);
    if (result < 0) { report(code == EPERM || code == EACCES ? "denied" : "error", code); return; }
    int status = 0;
    pid_t waited;
    do { waited = waitpid(target.pid, &status, WUNTRACED); } while (waited < 0 && errno == EINTR);
    if (waited != target.pid || !WIFSTOPPED(status)) {
        if (waited == target.pid) target.pid = -1; // Already reaped; never signal a reused PID.
        report("error", errno); return;
    }
#if defined(__APPLE__)
    if (ptrace(PT_DETACH, target.pid, reinterpret_cast<char*>(1), 0) < 0) {
#else
    if (ptrace(PTRACE_DETACH, target.pid, nullptr, nullptr) < 0) {
#endif
        report("error", errno); return;
    }
    report("attach_succeeded");
}

#if defined(__APPLE__)
void memory(Target& target, bool control) {
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t code = task_for_pid(mach_task_self(), target.pid, &task);
    if (code != KERN_SUCCESS) { report("task_port_unavailable", code); return; }
    mach_vm_address_t address = target.address;
    if (!control) {
        address = 0;
        // Read a single byte from a readable mapping; never print its contents.
        for (unsigned int i = 0; i < 4096; ++i) {
            mach_vm_size_t size = 0;
            vm_region_basic_info_data_64_t info{};
            mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
            mach_port_t object = MACH_PORT_NULL;
            code = mach_vm_region(task, &address, &size, VM_REGION_BASIC_INFO_64,
                                 reinterpret_cast<vm_region_info_t>(&info), &count, &object);
            if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
            if (code != KERN_SUCCESS || (info.protection & VM_PROT_READ)) break;
            if (size == 0 || address + size < address) break;
            address += size;
        }
    }
    unsigned char byte = 0;
    mach_vm_size_t size = 0;
    if (code == KERN_SUCCESS)
        code = mach_vm_read_overwrite(task, address, 1, reinterpret_cast<mach_vm_address_t>(&byte), &size);
    mach_port_deallocate(mach_task_self(), task);
    if (code == KERN_SUCCESS && size == 1)
        report(!control || byte == MARKER ? "read_succeeded" : "control_mismatch");
    else report("task_port_acquired_read_failed", code);
}
#else
uintptr_t readable_address(pid_t pid) {
    std::string path = "/proc/" + std::to_string(pid) + "/maps";
    FILE* maps = std::fopen(path.c_str(), "r");
    if (!maps) return 1; // An invalid address is never counted as a denied read.
    char line[4096], permissions[5];
    unsigned long long start = 0, end = 0;
    uintptr_t address = 1;
    while (std::fgets(line, sizeof(line), maps)) {
        if (std::sscanf(line, "%llx-%llx %4s", &start, &end, permissions) == 3 && permissions[0] == 'r') {
            address = uintptr_t(start); break;
        }
    }
    std::fclose(maps);
    return address;
}

void memory(Target& target, bool control, bool proc_mem) {
    uintptr_t address = control ? target.address : readable_address(target.pid);
    unsigned char byte = 0;
    ssize_t count;
    if (proc_mem) {
        std::string path = "/proc/" + std::to_string(target.pid) + "/mem";
        int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
        if (fd < 0) { read_result(-1, byte, control, errno); return; }
        count = pread(fd, &byte, 1, off_t(address));
        int code = errno;
        close(fd);
        read_result(count, byte, control, code);
    } else {
        struct iovec local{&byte, 1}, remote{reinterpret_cast<void*>(address), 1};
        count = process_vm_readv(target.pid, &local, 1, &remote, 1, 0);
        read_result(count, byte, control, errno);
    }
}
#endif
}

int main(int argc, char** argv) {
    struct rlimit core{0, 0};
    if (setrlimit(RLIMIT_CORE, &core) != 0) return 2;
    std::signal(SIGPIPE, SIG_IGN);
    try {
        if (argc == 3 && std::string(argv[1]) == "--fixture") {
            int fd = std::stoi(argv[2]);
            volatile unsigned char marker = MARKER;
            uintptr_t address = reinterpret_cast<uintptr_t>(&marker);
            exact(fd, &address, sizeof(address), true);
            char command;
            while (read(fd, &command, 1) < 0 && errno == EINTR) {}
            return 0;
        }
        if (argc != 4) return 2;
        bool control = std::string(argv[1]) == "control";
        if (!control && std::string(argv[1]) != "worker") return 2;
        Target target;
        target.start(argv[3], control);
        std::string method = argv[2];
        if (method == "ptrace" || method == "ptrace_exceptions")
            trace(target, method == "ptrace_exceptions");
#if defined(__APPLE__)
        else if (method == "mach_vm_read") memory(target, control);
#else
        else if (method == "process_vm_readv" || method == "proc_mem")
            memory(target, control, method == "proc_mem");
#endif
        else return 2;
    } catch (...) { report("probe_failed"); return 2; }
    return 0;
}

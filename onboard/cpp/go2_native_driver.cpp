// InternNav ROS bridge replacement using the vendor's precompiled SDK2/DDS.
// No automatic standing, gait changes, or service switches.
#include "native_command_guard.hpp"
#include <unitree/idl/go2/LowState_.hpp>
#include <unitree/idl/go2/SportModeState_.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>
#include <unitree/robot/go2/sport/sport_client.hpp>
#include <arpa/inet.h>
#include <fcntl.h>
#include <net/if.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace {
volatile std::sig_atomic_t interrupted = 0;
void Signal(int value) { interrupted = value; }
double Now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
void Usage() {
    std::cout << "Usage: go2_native_driver [--interface eth0] [--dry-run] "
                 "[--enable-motion --allowed-modes 1,3] [--state-port 8898]\n"
                 "Loopback UDP 8899, <ff vx,yaw>; default dry-run. 30s/3m session limits.\n"
                 "Fresh state JSON is sent to loopback state-port (0 disables).\n"
                 "Determine allowed ready modes with go2_state_probe first.\n";
}
struct Options { std::string interface = "eth0"; bool motion = false; unsigned state_port = 8898; std::set<unsigned> modes; };
Options Parse(int argc, char** argv) {
    Options result;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        if (key == "--enable-motion") result.motion = true;
        else if (key == "--dry-run") result.motion = false;
        else if (key == "--interface" && i + 1 < argc) result.interface = argv[++i];
        else if (key == "--state-port" && i + 1 < argc) {
            const std::string value = argv[++i]; std::size_t used = 0;
            const auto port = std::stoul(value, &used);
            if (used != value.size() || port > 65535) throw std::invalid_argument("Invalid state port");
            result.state_port = port;
        }
        else if (key == "--allowed-modes" && i + 1 < argc) {
            const std::string modes = argv[++i];
            if (modes != "1" && modes != "3" && modes != "1,3" && modes != "3,1")
                throw std::invalid_argument("Allowed modes must explicitly select 1 and/or 3");
            if (modes.find('1') != std::string::npos) result.modes.insert(1);
            if (modes.find('3') != std::string::npos) result.modes.insert(3);
        } else throw std::invalid_argument("Unknown or incomplete option: " + key);
    }
    if (!if_nametoindex(result.interface.c_str())) throw std::invalid_argument("Interface does not exist");
    if (result.state_port == 8899) throw std::invalid_argument("State port cannot equal command port 8899");
    if (result.motion && result.modes.empty()) throw std::invalid_argument("Motion requires explicit --allowed-modes after state verification");
    const std::uint16_t endian = 1;
    if (*reinterpret_cast<const unsigned char*>(&endian) != 1 || sizeof(float) != 4)
        throw std::runtime_error("The ROS <ff protocol requires little-endian float32");
    return result;
}
struct Socket {
    int fd = -1;
    Socket() {
        fd = socket(AF_INET, SOCK_DGRAM, 0);
        if (fd < 0) throw std::runtime_error("Cannot create UDP socket");
        sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(8899);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) ||
            fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
            close(fd); fd = -1; throw std::runtime_error("Cannot bind 127.0.0.1:8899 (another driver may own it)");
        }
    }
    ~Socket() { if (fd >= 0) close(fd); }
};
struct Session {
    explicit Session(const std::string& interface) { unitree::robot::ChannelFactory::Instance()->Init(0, interface); }
    ~Session() { try { unitree::robot::ChannelFactory::Instance()->Release(); } catch (...) {} }
};
struct StateOutput {
    int fd = -1; sockaddr_in target{};
    explicit StateOutput(unsigned port) {
        if (!port) return;
        fd = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK, 0);
        if (fd < 0) throw std::runtime_error("Cannot create state UDP socket");
        target.sin_family = AF_INET; target.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        target.sin_port = htons(port);
    }
    ~StateOutput() { if (fd >= 0) close(fd); }
    void Send(const std::string& value) {
        if (fd >= 0) sendto(fd, value.data(), value.size(), 0,
                          reinterpret_cast<const sockaddr*>(&target), sizeof(target));
    }
};
template<class V> bool Finite3(const V& value) {
    return std::isfinite(value[0]) && std::isfinite(value[1]) && std::isfinite(value[2]);
}
template<class V> void Vector3(std::ostringstream& out, const V& value) {
    out << '[' << value[0] << ',' << value[1] << ',' << value[2] << ']';
}
struct Motion {
    std::unique_ptr<unitree::robot::go2::SportClient> client;
    bool owned = false;
    explicit Motion(bool enable) {
        if (enable) { client.reset(new unitree::robot::go2::SportClient()); client->SetTimeout(0.2f); client->Init(); }
    }
    void Stop() {
        if (client && owned && client->StopMove() == 0) owned = false;
    }
    ~Motion() {
        for (int attempt = 0; owned && attempt < 3; ++attempt) {
            try { Stop(); } catch (...) {}
        }
        if (owned) std::cerr << "STOP acknowledgement missing; use physical remote takeover.\n";
    }
};
}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--help") { Usage(); return 0; }
    try {
        const auto options = Parse(argc, argv);
        std::signal(SIGINT, Signal); std::signal(SIGTERM, Signal);
        Socket socket;
        StateOutput state_output(options.state_port);
        Session session(options.interface);
        Motion motion(options.motion);
        internnav::NativeCommandGuard guard(options.modes);
        std::mutex mutex;
        double next_state = 0;
        unsigned long long sample_index = 0;
        unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_> sport("rt/sportmodestate");
        unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_> low("rt/lowstate");
        sport.InitChannel([&](const void* data) {
            if (!data || interrupted) return;
            const auto& value = *static_cast<const unitree_go::msg::dds_::SportModeState_*>(data);
            const double now = Now();
            const auto received_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count();
            std::lock_guard<std::mutex> lock(mutex);
            if (!Finite3(value.position()) || !Finite3(value.imu_state().rpy()) ||
                !Finite3(value.velocity()) || !std::isfinite(value.yaw_speed())) {
                const float invalid = std::numeric_limits<float>::quiet_NaN();
                guard.State(value.mode(), {invalid,invalid,invalid}, now);
                return;
            }
            guard.State(value.mode(), value.position(), now);
            ++sample_index;
            if (now < next_state) return;
            next_state = now + 0.05;  // At most 20Hz, only on a new state callback.
            std::ostringstream json; json.precision(9);
            json << "{\"receive_monotonic_ns\":" << received_ns
                 << ",\"sample_index\":" << sample_index
                 << ",\"mode\":" << static_cast<unsigned>(value.mode()) << ",\"position\":";
            Vector3(json, value.position()); json << ",\"rpy\":";
            Vector3(json, value.imu_state().rpy()); json << ",\"velocity\":";
            Vector3(json, value.velocity()); json << ",\"yaw_speed\":" << value.yaw_speed() << '}';
            state_output.Send(json.str());
        }, 1);
        low.InitChannel([&](const void* data) {
            if (!data || interrupted) return;
            const auto& value = *static_cast<const unitree_go::msg::dds_::LowState_*>(data);
            std::lock_guard<std::mutex> lock(mutex); guard.Remote(value.wireless_remote(), Now());
        }, 1);
        std::cerr << (options.motion ? "MOTION ENABLED" : "DRY RUN")
                  << ": waiting for fresh state and local UDP; exits in 30 seconds.\n";
        const double deadline = Now() + 30;
        double next_print = 0;
        while (!interrupted && Now() < deadline) {
            // Bound each drain so an abusive sender cannot starve the watchdog.
            for (int count = 0; count < 256; ++count) {
                unsigned char packet[9]; const auto size = recv(socket.fd, packet, sizeof(packet), 0);
                if (size < 0) {
                    if (errno != EAGAIN && errno != EWOULDBLOCK) { std::lock_guard<std::mutex> lock(mutex); guard.Lock("socket_error"); }
                    break;
                }
                std::lock_guard<std::mutex> lock(mutex); guard.Accept(packet, size, Now());
                if (count == 255) guard.Lock("udp_flood");
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                const auto command = guard.Current(Now());
                if (Now() >= next_print) {
                    std::cout << "{\"dry_run\":" << (options.motion ? "false" : "true")
                              << ",\"vx\":" << command.vx << ",\"yaw\":" << command.yaw
                              << ",\"reason\":\"" << command.reason << "\"}" << std::endl;
                    next_print = Now() + 0.5;
                }
                if (motion.client && !interrupted && (command.vx || command.yaw)) {
                    motion.owned = true;
                    if (motion.client->Move(command.vx, 0, command.yaw) != 0) {
                        guard.Lock("move_rpc_failed"); motion.Stop();
                    }
                } else motion.Stop();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(40));
        }
        motion.Stop(); low.CloseChannel(); sport.CloseChannel();
        if (motion.owned) {
            std::cerr << "STOP acknowledgement missing at shutdown.\n";
            return 1;
        }
        return interrupted ? 128 + interrupted : 0;
    } catch (const std::exception& error) {
        std::cerr << "Native driver stopped: " << error.what() << '\n'; return 1;
    } catch (...) { std::cerr << "Native driver stopped with SDK/DDS error\n"; return 1; }
}

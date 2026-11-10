// Read-only SDK2 state subscriber. This file contains no motion client or
// publisher. The SDK position is an estimate in its own local state frame;
// receiving it does not establish a map or a navigation goal transform.
#include <unitree/idl/go2/SportModeState_.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <net/if.h>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
volatile std::sig_atomic_t stop_signal = 0;
void HandleSignal(int signal) { stop_signal = signal; }

struct Options {
    std::string interface = "eth0";
    double seconds = 10.0;
    double rate = 2.0;
};

void Usage() {
    std::cout << "Usage: go2_state_probe [--interface eth0] [--seconds 10] [--rate 2]\n"
              << "Subscribe only to rt/sportmodestate; JSON output; no movement commands.\n";
}

double Number(const std::string& argument) {
    std::size_t consumed = 0;
    const double value = std::stod(argument, &consumed);
    if (consumed != argument.size() || !std::isfinite(value)) {
        throw std::invalid_argument("Expected a finite number: " + argument);
    }
    return value;
}

Options Parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (i + 1 >= argc) throw std::invalid_argument("Missing value for " + argument);
        if (argument == "--interface") options.interface = argv[++i];
        else if (argument == "--seconds") options.seconds = Number(argv[++i]);
        else if (argument == "--rate") options.rate = Number(argv[++i]);
        else throw std::invalid_argument("Unknown option: " + argument);
    }
    if (options.seconds <= 0 || options.seconds > 300) {
        throw std::invalid_argument("--seconds must be in (0, 300]");
    }
    if (options.rate <= 0 || options.rate > 100) {
        throw std::invalid_argument("--rate must be in (0, 100]");
    }
    if (options.interface.empty() ||
        options.interface.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-") != std::string::npos ||
        if_nametoindex(options.interface.c_str()) == 0) {
        throw std::invalid_argument("Network interface does not exist or has an invalid name: " + options.interface);
    }
    return options;
}

void Value(float value) {
    if (std::isfinite(value)) std::cout << value;
    else std::cout << "null";
}

template <class Vector>
void Vector3(const Vector& values) {
    std::cout << '[';
    for (int i = 0; i < 3; ++i) {
        if (i) std::cout << ',';
        Value(values[i]);
    }
    std::cout << ']';
}

struct FactorySession {
    explicit FactorySession(const std::string& interface) {
        unitree::robot::ChannelFactory::Instance()->Init(0, interface);
    }
    ~FactorySession() {
        try { unitree::robot::ChannelFactory::Instance()->Release(); }
        catch (...) {}  // Destruction must not throw, including after SIGINT.
    }
};

struct Samples {
    std::atomic<unsigned long long> received{0};
    std::atomic<unsigned long long> printed{0};
    std::mutex mutex;
    std::chrono::steady_clock::time_point next_print{};
};
}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--help") {
        Usage();
        return 0;  // --help never initializes DDS.
    }
    try {
        const Options options = Parse(argc, argv);
        std::signal(SIGINT, HandleSignal);
        std::signal(SIGTERM, HandleSignal);
        Samples samples;
        FactorySession session(options.interface);
        unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>
            subscriber("rt/sportmodestate");
        subscriber.InitChannel([&](const void* message) {
            if (stop_signal || !message) return;
            const auto& state = *static_cast<const unitree_go::msg::dds_::SportModeState_*>(message);
            const auto index = ++samples.received;
            const auto now = std::chrono::steady_clock::now();
            std::lock_guard<std::mutex> lock(samples.mutex);
            if (now < samples.next_print) return;
            samples.next_print = now + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(1.0 / options.rate));
            const auto unix_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            const auto monotonic_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now.time_since_epoch()).count();
            std::cout << std::setprecision(9)
                      << "{\"receive_time_unix_ms\":" << unix_ms
                      << ",\"receive_time_monotonic_ms\":" << monotonic_ms
                      << ",\"sample_index\":" << index
                      << ",\"mode\":" << static_cast<unsigned>(state.mode())
                      << ",\"gait_type\":" << static_cast<unsigned>(state.gait_type())
                      << ",\"position\":";
            Vector3(state.position());
            std::cout << ",\"rpy\":";
            Vector3(state.imu_state().rpy());
            std::cout << ",\"velocity\":";
            Vector3(state.velocity());
            std::cout << ",\"yaw_speed\":";
            Value(state.yaw_speed());
            std::cout << "}" << std::endl;
            ++samples.printed;
        }, 1);
        std::cerr << "Reading rt/sportmodestate on " << options.interface << " for "
                  << options.seconds << " seconds; output at most " << options.rate << " Hz.\n";
        const auto deadline = std::chrono::steady_clock::now() +
            std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<double>(options.seconds));
        while (!stop_signal && std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        subscriber.CloseChannel();
        std::cerr << "Received " << samples.received.load() << " state samples; printed "
                  << samples.printed.load() << ".\n";
        if (stop_signal) return 128 + stop_signal;
        if (!samples.received.load()) {
            std::cerr << "No state received. Check the internal network and active robot services; "
                      << "this probe does not switch services or change the interface.\n";
            return 2;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "State probe failed: " << error.what() << '\n';
        return 1;
    } catch (...) {
        std::cerr << "State probe failed with an SDK/DDS exception.\n";
        return 1;
    }
}

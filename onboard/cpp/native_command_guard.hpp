#pragma once
// The ROS bridge sends exactly two little-endian float32 values: vx, yaw.
// It has no sender timestamp/sequence; this guard bounds receipt age only.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <set>
#include <string>
#include <utility>

namespace internnav {
struct Command { double vx = 0, yaw = 0; std::string reason; };
class NativeCommandGuard {
public:
    explicit NativeCommandGuard(std::set<unsigned> modes) : modes_(std::move(modes)) {}
    void State(unsigned mode, const std::array<float, 3>& position, double now) {
        state_valid_ = std::isfinite(now);
        for (float value : position) state_valid_ &= std::isfinite(value);
        if (!state_valid_) { if (active_) Lock("invalid_state"); return; }
        if (have_position_ && active_) {
            const double step = std::hypot(position[0] - position_[0], position[1] - position_[1]);
            if (step > 0.5) Lock("odometry_jump");
            else travelled_ += step;
            if (travelled_ >= 3.0) Lock("distance_budget");
        }
        mode_ = mode; position_ = position; have_position_ = true; state_time_ = now;
    }
    void Remote(const std::array<std::uint8_t, 40>& bytes, double now) {
        remote_valid_ = std::isfinite(now);
        std::uint16_t buttons = 0; std::memcpy(&buttons, bytes.data() + 2, 2);
        bool override = buttons != 0;
        // SDK wireless layout: lx/rx/ry at 4/8/12, analog L2 at16, ly at20.
        for (unsigned offset : {4u, 8u, 12u, 20u}) {
            float axis; std::memcpy(&axis, bytes.data() + offset, 4);
            if (!std::isfinite(axis) || std::abs(axis) > 1.1) remote_valid_ = false;
            override |= std::abs(axis) > 0.12;
        }
        remote_time_ = now;
        if (!remote_valid_ || override) Lock(override ? "remote_override" : "invalid_remote");
    }
    bool Accept(const void* bytes, std::size_t length, double now) {
        float values[2];
        if (length != sizeof(values) || !std::isfinite(now)) { Lock("invalid_command"); return false; }
        std::memcpy(values, bytes, sizeof(values));
        if (!std::isfinite(values[0]) || !std::isfinite(values[1])) { Lock("invalid_command"); return false; }
        vx_ = std::clamp<double>(values[0], 0, 0.15);
        yaw_ = std::clamp<double>(values[1], -0.3, 0.3);
        packet_time_ = now;
        return true;
    }
    Command Current(double now) {
        if (!locked_.empty()) return {0, 0, locked_};
        if (!std::isfinite(now) || !state_valid_ || !remote_valid_ ||
            now < state_time_ || now < remote_time_ ||
            now - state_time_ >= 0.5 || now - remote_time_ >= 0.5) { Clear(); return {0, 0, "state_unavailable"}; }
        if (!modes_.count(mode_)) { Clear(); return {0, 0, "mode_not_allowed"}; }
        if (now < packet_time_ || now - packet_time_ >= 0.5) { Clear(); return {0, 0, "command_expired"}; }
        if (active_ && now - started_ >= 30) { Lock("time_budget"); return {0, 0, locked_}; }
        if ((vx_ || yaw_) && !active_) { active_ = true; started_ = now; travelled_ = 0; }
        return {vx_, yaw_, vx_ || yaw_ ? "command" : "stop"};
    }
    void Clear() { vx_ = yaw_ = 0; packet_time_ = -std::numeric_limits<double>::infinity(); }
    void Lock(const std::string& reason) { if (locked_.empty()) locked_ = reason; Clear(); }
private:
    std::set<unsigned> modes_;
    std::array<float, 3> position_{};
    unsigned mode_ = 0;
    bool state_valid_ = false, remote_valid_ = false, have_position_ = false, active_ = false;
    double state_time_ = 0, remote_time_ = 0, packet_time_ = -std::numeric_limits<double>::infinity();
    double vx_ = 0, yaw_ = 0, started_ = 0, travelled_ = 0;
    std::string locked_;
};
}  // namespace internnav

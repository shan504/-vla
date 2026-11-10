"""Compile and exercise the SDK-independent native command guard; no DDS or robot."""
import shutil
import subprocess
from pathlib import Path

import pytest


SOURCE = r'''
#include "native_command_guard.hpp"
#include <cassert>
#include <limits>
#include <string>
using internnav::NativeCommandGuard;
std::array<uint8_t,40> neutral{};
void ready(NativeCommandGuard& guard, double now, unsigned mode=1) {
    guard.State(mode, {0,0,0}, now); guard.Remote(neutral, now);
}
void packet(NativeCommandGuard& guard, double now, float v=.1f, float w=.2f) {
    float values[2]{v,w}; assert(guard.Accept(values, sizeof(values), now));
}
void zero(NativeCommandGuard& guard, double now, const std::string& reason) {
    auto c=guard.Current(now); assert(c.vx==0 && c.yaw==0 && c.reason==reason);
}
int main(int argc,char** argv) {
    const std::string name=argv[1]; NativeCommandGuard guard({1,3});
    if(name=="fresh") {
        ready(guard,10); packet(guard,10,9,9); auto c=guard.Current(10);
        assert(c.vx==.15 && c.yaw==.3 && c.reason=="command");
        packet(guard,10.1,-9,-9); c=guard.Current(10.1); assert(c.vx==0 && c.yaw==-.3);
    } else if(name=="startup") {
        packet(guard,10); zero(guard,10,"state_unavailable");
        ready(guard,10.1); zero(guard,10.1,"command_expired");
    } else if(name=="mode") {
        ready(guard,10,5); packet(guard,10); zero(guard,10,"mode_not_allowed");
        ready(guard,10.1); zero(guard,10.1,"command_expired");
        NativeCommandGuard unverified({}); ready(unverified,10); packet(unverified,10);
        zero(unverified,10,"mode_not_allowed");
    } else if(name=="stale") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        guard.Remote(neutral,11); packet(guard,11); zero(guard,11,"state_unavailable");
        ready(guard,11.1); zero(guard,11.1,"command_expired");
        packet(guard,11.1); assert(guard.Current(11.1).vx>0);
        guard.State(1,{0,0,0},12); packet(guard,12); zero(guard,12,"state_unavailable");
    } else if(name=="watchdog") {
        ready(guard,10); packet(guard,10); assert(guard.Current(10).vx>0);
        ready(guard,10.5); zero(guard,10.5,"command_expired");
    } else if(name=="malformed") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        char bad[9]{}; assert(!guard.Accept(bad,sizeof(bad),10.1));
        packet(guard,10.2); zero(guard,10.2,"invalid_command");
        NativeCommandGuard nan({1}); ready(nan,10);
        float bad_float[2]{std::numeric_limits<float>::quiet_NaN(),0};
        assert(!nan.Accept(bad_float,sizeof(bad_float),10)); zero(nan,10,"invalid_command");
    } else if(name=="remote") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        auto bytes=neutral; float axis=.2f; std::memcpy(bytes.data()+20,&axis,4);
        guard.Remote(bytes,10.1); ready(guard,10.2); packet(guard,10.2);
        zero(guard,10.2,"remote_override");
        NativeCommandGuard button({1}); ready(button,10); bytes=neutral; bytes[2]=1;
        button.Remote(bytes,10); zero(button,10,"remote_override");
    } else if(name=="distance") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        for(int i=1;i<=8;++i) {
            double t=10+i*.01;
            guard.State(1,{i%2?.4f:0.f,0,0},t); guard.Remote(neutral,t); packet(guard,t);
        }
        zero(guard,10.08,"distance_budget");
    } else if(name=="time") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        ready(guard,40); packet(guard,40); zero(guard,40,"time_budget");
    } else if(name=="bad_pose") {
        ready(guard,10); packet(guard,10); guard.Current(10);
        guard.State(1,{1,0,0},10.1); zero(guard,10.1,"odometry_jump");
        NativeCommandGuard invalid({1}); ready(invalid,10); packet(invalid,10); invalid.Current(10);
        invalid.State(1,{std::numeric_limits<float>::quiet_NaN(),0,0},10.1);
        zero(invalid,10.1,"invalid_state");
    } else { return 2; }
}
'''


@pytest.fixture(scope='module')
def guard_binary(tmp_path_factory):
    compiler = shutil.which('g++')
    if not compiler:
        pytest.skip('g++ is required for native guard checks')
    folder = tmp_path_factory.mktemp('native-command-guard')
    source = folder / 'guard.cpp'
    source.write_text(SOURCE)
    binary = folder / 'guard-test'
    include = Path(__file__).resolve().parents[2] / 'onboard/cpp'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-I', str(include),
                    str(source), '-o', str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize('case', ['fresh', 'startup', 'mode', 'stale', 'watchdog',
                                'malformed', 'remote', 'distance', 'time', 'bad_pose'])
def test_native_guard_rejects_stale_or_unsafe_commands(guard_binary, case):
    subprocess.run([str(guard_binary), case], check=True, capture_output=True, text=True)

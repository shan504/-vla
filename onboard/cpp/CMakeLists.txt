cmake_minimum_required(VERSION 3.10)
project(go2_state_probe LANGUAGES CXX)

# Tested against the official SDK2 source at:
# https://github.com/unitreerobotics/unitree_sdk2/tree/aaf69cb56c9d5ed63d8b284008ec3a8ab999bec1
# Example: cmake -S onboard/cpp -B .tools/robot-state-build \
#   -DUNITREE_SDK2_DIR=/absolute/path/to/unitree_sdk2 \
#   -DCMAKE_CXX_COMPILER=/usr/bin/g++
set(UNITREE_SDK2_DIR "" CACHE PATH "Official unitree_sdk2 source directory")
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "The official SDK2 precompiled libraries require Linux")
endif()
if(NOT UNITREE_SDK2_DIR)
    message(FATAL_ERROR "Set -DUNITREE_SDK2_DIR=/absolute/path/to/unitree_sdk2")
endif()
get_filename_component(UNITREE_SDK2_DIR "${UNITREE_SDK2_DIR}" ABSOLUTE)
foreach(required
        CMakeLists.txt
        include/unitree/idl/go2/SportModeState_.hpp
        lib/${CMAKE_SYSTEM_PROCESSOR}/libunitree_sdk2.a
        thirdparty/lib/${CMAKE_SYSTEM_PROCESSOR}/libddsc.so
        thirdparty/lib/${CMAKE_SYSTEM_PROCESSOR}/libddscxx.so)
    if(NOT EXISTS "${UNITREE_SDK2_DIR}/${required}")
        message(FATAL_ERROR "Missing official SDK2 file: ${UNITREE_SDK2_DIR}/${required}")
    endif()
endforeach()

# Import existing architecture-specific libraries; do not build or run robot
# examples and do not install SDK/DDS into the system.
set(BUILD_EXAMPLES OFF CACHE BOOL "Do not build SDK control examples" FORCE)
unset(UNITREE_SDK_LIB CACHE)
add_subdirectory("${UNITREE_SDK2_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/unitree-sdk2")
add_executable(go2_state_probe go2_state_probe.cpp)
target_compile_features(go2_state_probe PRIVATE cxx_std_17)
target_link_libraries(go2_state_probe PRIVATE unitree_sdk2)
set_target_properties(go2_state_probe PROPERTIES
    BUILD_RPATH "${UNITREE_SDK2_DIR}/thirdparty/lib/${CMAKE_SYSTEM_PROCESSOR}")
# On Linux GNU linkers, DT_RPATH takes priority over an inherited ROS
# LD_LIBRARY_PATH. DT_RUNPATH would allow an older ROS DDS library to replace
# the private SDK library even though CMake linked the intended vendor files.
set_property(TARGET go2_state_probe APPEND_STRING PROPERTY
    LINK_FLAGS " -Wl,--disable-new-dtags")

add_executable(go2_native_driver go2_native_driver.cpp)
target_compile_features(go2_native_driver PRIVATE cxx_std_17)
target_link_libraries(go2_native_driver PRIVATE unitree_sdk2)
set_target_properties(go2_native_driver PROPERTIES
    BUILD_RPATH "${UNITREE_SDK2_DIR}/thirdparty/lib/${CMAKE_SYSTEM_PROCESSOR}")
set_property(TARGET go2_native_driver APPEND_STRING PROPERTY
    LINK_FLAGS " -Wl,--disable-new-dtags")

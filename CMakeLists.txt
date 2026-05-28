cmake_minimum_required(VERSION 3.22)

project(polymarket_probability_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(probability_core
    cpp/probability_core/src/probability_core.cpp
)

target_include_directories(probability_core
    PUBLIC
        cpp/probability_core/include
)

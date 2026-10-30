CC ?= cc
CXX ?= c++
BUILD ?= build
RP_PREFIX ?= /opt/redpitaya
CPPFLAGS += -DCJSON_NESTING_LIMIT=64
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Werror -Wpedantic
CXXFLAGS ?= -O2 -std=c++20 -Wall -Wextra -Werror -Wpedantic
COMMON = $(BUILD)/commands.o $(BUILD)/nullifier.o $(BUILD)/stream.o $(BUILD)/cJSON.o

.PHONY: all board test test-sdk
all: $(BUILD)/rp-nullifier-replay
board: $(BUILD)/rp-nullifierd

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: %.c nullifier.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/cJSON.o: vendor/cJSON.c vendor/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/acquire_rp.o: acquire_rp.cpp nullifier.h | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include -c $< -o $@

$(BUILD)/rp-nullifier-replay: $(COMMON) $(BUILD)/acquire_stub.o
	$(CC) $(LDFLAGS) $^ -lm -pthread -o $@

$(BUILD)/rp-nullifierd: $(COMMON) $(BUILD)/acquire_rp.o
	$(CXX) $(LDFLAGS) $^ -L$(RP_PREFIX)/lib -Wl,-rpath,$(RP_PREFIX)/lib \
	  -lrp -lrp-hw-calib -lrp-hw-profiles -lm -pthread -o $@

test: all
	python3 tests/check_commands.py $(BUILD)/rp-nullifier-replay
	python3 tests/check_stream.py $(BUILD)/rp-nullifier-replay

$(BUILD)/acquire_mock.o: acquire_rp.cpp nullifier.h | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include \
	  -DNF_FPGA_INFO_PATH='"$(abspath $(BUILD))/fpga.inf"' \
	  -DNF_LOCK_PATH='"$(abspath $(BUILD))/acquisition.lock"' -c $< -o $@

$(BUILD)/mock_rp.o: tests/mock_rp.cpp | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include -c $< -o $@

$(BUILD)/rp-nullifier-mock: $(COMMON) $(BUILD)/acquire_mock.o $(BUILD)/mock_rp.o
	$(CXX) $(LDFLAGS) $^ -lm -pthread -o $@

test-sdk: $(BUILD)/rp-nullifier-mock
	python3 tests/check_acquisition.py $(BUILD)/rp-nullifier-mock

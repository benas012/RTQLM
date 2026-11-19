CC ?= cc
CXX ?= c++
BUILD ?= build
RP_PREFIX ?= /opt/redpitaya
CPPFLAGS += -DCJSON_NESTING_LIMIT=64
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Werror -Wpedantic
CXXFLAGS ?= -O2 -std=c++20 -Wall -Wextra -Werror -Wpedantic
# Zynq ARMv7 boards have NEON; other compiler targets retain the scalar kernel.
RAW_SIMD_FLAGS ?= $(if $(filter-out arm64%,$(filter arm%,$(shell $(CC) -dumpmachine))),-mfpu=neon,)
COMMON = $(BUILD)/commands.o $(BUILD)/nullifier.o $(BUILD)/stream.o $(BUILD)/cJSON.o

.PHONY: all board test test-sdk test-raw test-weighted test-statistics test-timing test-buffers test-cluster2d
all: $(BUILD)/rp-nullifier-replay
buffered: $(BUILD)/rp-buffered-server
buffered-board: $(BUILD)/rp-buffered-board
stream-board: $(BUILD)/ring-server $(BUILD)/gpio-monitor

$(BUILD)/ring-server: ring_server.cpp acquire_rp.cpp nullifier.h $(BUILD)/nullifier.o

	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include ring_server.cpp $(BUILD)/nullifier.o -L$(RP_PREFIX)/lib -Wl,-rpath,$(RP_PREFIX)/lib -lrp -lrp-hw-calib -lrp-hw-profiles -lm -pthread -o $@

$(BUILD)/gpio-monitor: gpio_monitor.cpp | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include $< -L$(RP_PREFIX)/lib -Wl,-rpath,$(RP_PREFIX)/lib -lrp -o $@

$(BUILD)/buffered_worker.o: buffered_worker.c commands.c nullifier.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/rp-buffered-server: $(BUILD)/buffered_worker.o $(BUILD)/nullifier.o $(BUILD)/stream.o $(BUILD)/cJSON.o $(BUILD)/acquire_stub.o
	$(CC) $(LDFLAGS) $^ -lm -pthread -o $@

$(BUILD)/rp-buffered-board: $(BUILD)/buffered_worker.o $(BUILD)/nullifier.o $(BUILD)/stream.o $(BUILD)/cJSON.o $(BUILD)/acquire_rp.o
	$(CXX) $(LDFLAGS) $^ -L$(RP_PREFIX)/lib -Wl,-rpath,$(RP_PREFIX)/lib -lrp -lrp-hw-calib -lrp-hw-profiles -lm -pthread -o $@
board: $(BUILD)/rp-nullifierd

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: %.c nullifier.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/nullifier.o: CFLAGS += $(RAW_SIMD_FLAGS)

$(BUILD)/cJSON.o: vendor/cJSON.c vendor/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/acquire_rp.o: acquire_rp.cpp nullifier.h | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include -c $< -o $@

$(BUILD)/rp-nullifier-replay: $(COMMON) $(BUILD)/acquire_stub.o
	$(CC) $(LDFLAGS) $^ -lm -pthread -o $@

$(BUILD)/rp-nullifierd: $(COMMON) $(BUILD)/acquire_rp.o
	$(CXX) $(LDFLAGS) $^ -L$(RP_PREFIX)/lib -Wl,-rpath,$(RP_PREFIX)/lib \
	  -lrp -lrp-hw-calib -lrp-hw-profiles -lm -pthread -o $@

test: all test-raw test-weighted test-statistics test-timing test-buffers test-cluster2d test-buffered test-e1-mask
	python3 tests/check_commands.py $(BUILD)/rp-nullifier-replay
	python3 tests/check_stream.py $(BUILD)/rp-nullifier-replay

.PHONY: test-buffered
.PHONY: test-e1-mask
$(BUILD)/check-e1-mask: tests/check_e1_mask.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@
test-e1-mask: $(BUILD)/check-e1-mask
	$(BUILD)/check-e1-mask
test-buffered: $(BUILD)/rp-buffered-server
	python3 tests/check_buffered_worker.py $(BUILD)/rp-buffered-server

$(BUILD)/check-raw-modes: tests/check_raw_modes.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. tests/check_raw_modes.c $(BUILD)/nullifier.o -lm -o $@

test-raw: $(BUILD)/check-raw-modes
	$(BUILD)/check-raw-modes

$(BUILD)/check-weighted: tests/check_weighted.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@

test-weighted: $(BUILD)/check-weighted
	$(BUILD)/check-weighted

$(BUILD)/check-statistics: tests/check_statistics.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@

test-statistics: $(BUILD)/check-statistics
	$(BUILD)/check-statistics

$(BUILD)/check-timing: tests/check_timing.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@

test-timing: $(BUILD)/check-timing
	$(BUILD)/check-timing

$(BUILD)/check-buffers: tests/check_buffers.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@

test-buffers: $(BUILD)/check-buffers
	$(BUILD)/check-buffers

$(BUILD)/check-cluster2d: tests/check_cluster2d.c $(BUILD)/nullifier.o nullifier.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -I. $< $(BUILD)/nullifier.o -lm -o $@

test-cluster2d: $(BUILD)/check-cluster2d
	$(BUILD)/check-cluster2d

$(BUILD)/acquire_mock.o: acquire_rp.cpp nullifier.h | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include \
	  -DNF_FPGA_INFO_PATH='"$(abspath $(BUILD))/fpga.inf"' \
	  -DNF_HOUSEKEEPING_PATH='"$(abspath $(BUILD))/housekeeping.bin"' -DNF_HOUSEKEEPING_BASE=0 \
	  -DNF_LOCK_PATH='"$(abspath $(BUILD))/acquisition.lock"' -c $< -o $@

$(BUILD)/mock_rp.o: tests/mock_rp.cpp | $(BUILD)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I$(RP_PREFIX)/include -c $< -o $@

$(BUILD)/rp-nullifier-mock: $(COMMON) $(BUILD)/acquire_mock.o $(BUILD)/mock_rp.o
	$(CXX) $(LDFLAGS) $^ -lm -pthread -o $@

test-sdk: $(BUILD)/rp-nullifier-mock
	python3 tests/check_acquisition.py $(BUILD)/rp-nullifier-mock


# Performance tools are maintained in the sibling benchmarks folder.
BENCHMARKS ?= $(abspath ../../../benchmarks)
-include $(BENCHMARKS)/native-targets.mk

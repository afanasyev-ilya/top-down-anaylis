import subprocess
import re
import copy
import os
import sys
#from .arch_properties import get_arch
#from .files import *
#from .roofline import kunpeng_characteristics

kunpeng_characteristics = {"bandwidths": {"DRAM": 187, "L3": 1060, "L2": 1800, "L1": 2200},
                           "peak_performances": {"float_no_vector_noFMA": 124,
                                                 "float_vector_noFMA": 499,
                                                 "float_vector_FMA": 1996}}  # GFLOP/s

tmp_perf_metrics_file_name = "metrics.txt"
tmp_timings_file_name = "timings.txt"
short_run = False

def code(event_code):
    codes = {"MEM_STALL_ANY_LOAD": "r7004",
             "MEM_STALL_ANY_STORE": "r7005",
             "EXEC_STALL_CYCLE": "r7001",
             "MEM_STALL_L1MISS": "r7006",
             "MEM_STALL_L2MISS": "r7007",
             "REMOTE_ACCESS": "r0031",
             "MEM_ACCESS_LD": "r0066",
             "MEM_ACCESS_ST": "r0067",
             "LL_CACHE": "r0032",
             "LL_CACHE_MISS": "r0033",
             "fetch_bubble": "r2014",
             "L1D_CACHE": "r0004",
             "L2D_CACHE": "r0016",
             "rd_spipe": "rd_spipe",
             "flux_rd": "r01",
             "flux_wr": "r00",
             "rd_hit_cpipe": "rd_hit_cpipe",
             "rd_hit_spipe": "rd_hit_spipe"
             }
    return codes[event_code]

def get_arch():  # returns architecture, eigher kunpeng or intel_xeon
    architecture = "unknown"
    output = subprocess.check_output(["lscpu"])
    arch_line = ""
    vendor_line = ""
    for item in output.decode().split("\n"):
        if "Architecture" in item:
            arch_line = item.strip()
        if "Vendor" in item or "ID" in item:
            vendor_line = item.strip()

    if "aarch64" in arch_line:
        architecture = "kunpeng"
    if "x86_64" in arch_line:
        if "Intel" in vendor_line:
            architecture = "intel_xeon"
        if "AMD" in vendor_line:
            architecture = "amd_epyc"
    return architecture

def merge_two_dicts(x, y):
    """Given two dictionaries, merge them into a new dict as a shallow copy."""
    z = {**x, **y}
    return z

def get_no_conflict_events_list(architecture):
    if architecture == "kunpeng":
        events = ["r7004", # MEM_STALL_ANY_LOAD
                  "r7005", # MEM_STALL_ANY_STORE
                  "r7001", # exec_stall_cycle
                  "r7006", # MEM_STALL_L1MISS
                  "r7007", # MEM_STALL_L2MISS
                  "r0031", # REMOTE_ACCESS"
                  "r0066", # MEM_ACCESS_LD
                  "r0067", # MEM_ACCESS_ST
                  "r0032", # LL_CACHE
                  "r0033",  # LL_CACHE_MISS
                  "duration_time",
                  "rd_spipe", #"rd_spipe":
                  "rd_hit_cpipe", #rd_hit_cpipe
                  "rd_hit_spipe" #rd_hit_spipe
                  ]
        return events
    if architecture == "intel_xeon":
        events = ["L1-dcache-loads",
                  "LLC-loads",
                  "LLC-stores",
                  "LLC-store-misses",
                  "LLC-load-misses"]
        return events
    return []


def get_conflicted_events_list(architecture):
    if architecture == "kunpeng":
        if short_run:
            return []
        events = ["r2014", #"fetch_bubble"
                  "CPU_CYCLES",
                  "INST_SPEC",
                  "INST_RETIRED",
                  "r0004", #"L1D_CACHE":
                  "r0016", #"L2D_CACHE":
                  "r01", #"flux_rd":
                  "r00" #"flux_wr"
                  ]
        return events
    return []

def get_event_value_from_file_line(line, event_list):
    for event_name in event_list:
        if event_name in line:
            event_value = int(re.search(r'\d+', line).group())
            return {event_name: event_value}
    return None

def run_and_wait(cmd, options):
    #print(cmd)
    os.environ['OMP_NUM_THREADS'] = "64"
    os.environ['OMP_PROC_BIND'] = "close"

    p = subprocess.Popen(cmd, shell=True,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    output, err = p.communicate()
    rc = p.returncode
    p_status = p.wait()
    string_output = err.decode("utf-8")
    return string_output

def collect_list_of_events(prog_name, prog_args, event_list): # can collect groups of events or single events
    result = {}
    events_args = [','.join(event_list)]
    cmd = "perf stat -a -e" + ','.join(events_args) + " " + prog_name + " " + ' '.join(prog_args)
    res = run_and_wait(cmd, "")

    lines = res.split('\n')
    for line in lines:
        new_metric = get_event_value_from_file_line(line, event_list)
        if new_metric is not None:
            result = merge_two_dicts(result, new_metric)
    return result

def analyse_events(architecture, hardware_events):
    all = copy.deepcopy(hardware_events)

    if architecture == "kunpeng":
        if not short_run:
            all["Frontend_Bound"] = 100.0* (all[code("fetch_bubble")]/(4.0 * all["CPU_CYCLES"]))
            all["Bad_Speculation"] = 100.0* ((all["INST_SPEC"] - all["INST_RETIRED"])/(4.0 * all["CPU_CYCLES"]))
            all["Retiring"] = 100.0* (all["INST_RETIRED"] / (4.0 * all["CPU_CYCLES"]))
            all["Backend_Bound"] = (100.0 - (all["Frontend_Bound"] + all["Bad_Speculation"] + all["Retiring"]))
            all["Memory_Bound"] = 100.0* ((all[code("MEM_STALL_ANY_LOAD")] + all[code("MEM_STALL_ANY_STORE")])/all[code("EXEC_STALL_CYCLE")])
            all["L1_Bound"] = 100.0* ((all[code("MEM_STALL_ANY_LOAD")] - all[code("MEM_STALL_L1MISS")])/all[code("EXEC_STALL_CYCLE")])
            all["L2_Bound"] = 100.0* ((all[code("MEM_STALL_L1MISS")] - all[code("MEM_STALL_L2MISS")])/all[code("EXEC_STALL_CYCLE")])
            all["L3_Bound_or_DRAM"] = 100.0* (all[code("MEM_STALL_L2MISS")]/all[code("EXEC_STALL_CYCLE")])
            all["Store_Bound"] = 100.0* (all[code("MEM_STALL_ANY_STORE")]/all[code("EXEC_STALL_CYCLE")])
            all["Core_Bound"] = 100.0* ((all[code("EXEC_STALL_CYCLE")] - all[code("MEM_STALL_ANY_LOAD")] - all[code("MEM_STALL_ANY_STORE")])/all[code("EXEC_STALL_CYCLE")])

            all["LL_hit_rate"] = 100.0* (1.0 - all[code("LL_CACHE_MISS")]/all[code("LL_CACHE")])
            all["LL_hit_rate2"] = 100.0*(all[code("rd_hit_cpipe")] + all[code("rd_hit_spipe")])/all[code("rd_spipe")]

            all["Remote_accesses"] = 100.0* (all[code("REMOTE_ACCESS")]/(all[code("MEM_ACCESS_LD")] + all[code("MEM_ACCESS_ST")]))

            all["L1_SBW"] = all[code("L1D_CACHE")] * 16 / (all["duration_time"])
            all["L1_SBW_percent"] = 100.0 * all["L1_SBW"] / kunpeng_characteristics["bandwidths"]["L1"]

            all["L2_SBW"] = all[code("L2D_CACHE")] * 64 / (all["duration_time"])
            all["L2_SBW_percent"] = 100.0 * all["L2_SBW"] / kunpeng_characteristics["bandwidths"]["L2"]

            all["L3_SBW"] = all[code("rd_spipe")] * 64 / (all["duration_time"])
            all["L3_SBW_percent"] = 100.0 * all["L3_SBW"] * 64 / kunpeng_characteristics["bandwidths"]["L3"]

            all["DRAM_SBW"] = (all[code("flux_rd")] + all[code("flux_wr")]) * 32 / (all["duration_time"])
            all["DRAM_SBW_percent"] = 100.0 * all["DRAM_SBW"] / kunpeng_characteristics["bandwidths"]["DRAM"]
        else:
            all["LL_hit_rate"] = 100.0* (1.0 - all[code("LL_CACHE_MISS")]/all[code("LL_CACHE")])
            all["LL_hit_rate2"] = 100.0*(all[code("rd_hit_cpipe")] + all[code("rd_hit_spipe")])/all[code("rd_spipe")]

    if architecture == "intel_xeon":
        all["LLC_hit_rate"] = 1.0 - (all["LLC-store-misses"] + all["LLC-load-misses"])/(all["LLC-stores"] + all["LLC-loads"])

    # remove hardware events from resulting dict
    for key in all.copy():
        if key in hardware_events:
            del all[key]

    return all

def print_metrics(metrics):
    print("\n****************** RESULTS ********************\n")
    one_tab = ["Memory_Bound", "Core_Bound"]
    double_tab = ["L1_Bound", "L2_Bound", "L3_Bound_or_DRAM", "Store_Bound"]
    for metric in metrics:
        if metric in one_tab:
            print("\t", end="")
        if metric in double_tab:
            print("\t\t", end="")
        print(str(metric) + ": " + str(metrics[metric]))

def run_top_down_profiling(prog_name, prog_args):
    arch = get_arch()

    no_conflict_event_list = get_no_conflict_events_list(arch)
    conflicted_event_list = get_conflicted_events_list(arch)

    hardware_events = {}
    hardware_events = merge_two_dicts(hardware_events, collect_list_of_events(prog_name, prog_args, no_conflict_event_list))
    for conflicted_event in conflicted_event_list:
        hardware_events = merge_two_dicts(hardware_events, collect_list_of_events(prog_name, prog_args, [conflicted_event]))

    app_metrics = analyse_events(arch, hardware_events)
    return app_metrics

if __name__ == "__main__":
    prog_name = sys.argv[1]
    args = sys.argv[2:len(sys.argv)]

    arch = get_arch()
    metrics = run_top_down_profiling(prog_name, args)

    print_metrics(metrics)

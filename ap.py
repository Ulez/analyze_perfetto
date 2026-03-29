import argparse
import sys
from perfetto.trace_processor import TraceProcessor

def main():
    parser = argparse.ArgumentParser(description="Perfetto Trace 自动化 CPU 负载分析工具")
    parser.add_argument("trace_file", help="Perfetto trace 文件路径")
    parser.add_argument("--t1", type=float, required=True, help="起始时间 (秒，相对于 trace 开头)")
    parser.add_argument("--t2", type=float, required=True, help="结束时间 (秒，相对于 trace 开头)")
    parser.add_argument("--process", type=str, required=True, help="需要详细分析的指定进程名")
    
    args = parser.parse_args()

    if args.t1 >= args.t2:
        print("错误: t1 必须小于 t2")
        sys.exit(1)

    print(f"正在加载 Trace: {args.trace_file} ...")
    tp = TraceProcessor(trace=args.trace_file)

    # 1. 获取 Trace 基础时间边界
    bounds = tp.query_dict("SELECT start_ts, end_ts FROM trace_bounds")[0]
    trace_start_ts = bounds['start_ts']
    trace_end_ts = bounds['end_ts']

    # 2. 计算相对时间的纳秒绝对值
    w_start = trace_start_ts + int(args.t1 * 1e9)
    w_end = trace_start_ts + int(args.t2 * 1e9)
    
    if w_start > trace_end_ts or w_end < trace_start_ts:
        print("错误: 指定的时间窗口超出了 Trace 的实际时间范围")
        sys.exit(1)
        
    w_start = max(w_start, trace_start_ts)
    w_end = min(w_end, trace_end_ts)
    w_dur = w_end - w_start

    print(f"分析时间窗口: {args.t1}s - {args.t2}s (持续 {w_dur / 1e9:.3f} 秒)\n")

    # 3. 构建核心时间裁剪 CTE (Common Table Expression)
    # 逻辑: 过滤掉不在窗口内的 sched，并严格将 ts 和 dur 裁剪到窗口边界内
    clipped_sched_cte = f"""
    WITH clipped_sched AS (
        SELECT 
            cpu,
            utid,
            MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as c_dur
        FROM sched
        WHERE ts < {w_end} AND ts + dur > {w_start} AND dur > 0
    )
    """

    # --- 任务 A: 计算系统总 CPU 负载 ---
    # 获取 CPU 核心数
    cpus_result = tp.query_dict("SELECT COUNT(DISTINCT cpu) as num_cpus FROM sched")
    num_cpus = cpus_result[0]['num_cpus'] if cpus_result else 8

    sys_load_query = clipped_sched_cte + f"""
    SELECT SUM(c_dur) as active_time_ns
    FROM clipped_sched
    """
    sys_load_res = tp.query_dict(sys_load_query)[0]
    total_active_ns = sys_load_res['active_time_ns'] or 0
    # 100% = 1个核心跑满。系统总上限为 num_cpus * 100%
    overall_load = (total_active_ns / w_dur) * 100 
    
    print("-" * 50)
    print(f"【系统整体负载】")
    print(f"总活动核心数: {overall_load / 100:.2f} 核心 (共 {num_cpus} 核心)")
    print(f"整体 CPU 使用率: {overall_load / num_cpus:.2f} % (平均每个核心)")
    print("-" * 50)

    # --- 任务 B: 各进程 TOP CPU 占用 ---
    top_proc_query = clipped_sched_cte + f"""
    SELECT 
        IFNULL(process.name, 'Unknown') as process_name,
        process.pid,
        SUM(c_dur) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM clipped_sched
    JOIN thread USING (utid)
    LEFT JOIN process USING (upid)
    GROUP BY process.upid
    ORDER BY cpu_pct DESC
    LIMIT 10
    """
    print("\n【Top 10 进程 CPU 占用 (100% = 1个核心满载)】")
    print(f"{'PID':<10} {'CPU %':<10} {'进程名'}")
    for row in tp.query_dict(top_proc_query):
        print(f"{str(row['pid']):<10} {row['cpu_pct']:<10.2f} {row['process_name']}")
    print("-" * 50)

    # --- 任务 C: 指定进程的各线程 CPU 占用 ---
    target_threads_query = clipped_sched_cte + f"""
    SELECT 
        thread.name as thread_name,
        thread.tid,
        SUM(c_dur) / CAST({w_dur} AS FLOAT) * 100 as cpu_pct
    FROM clipped_sched
    JOIN thread USING (utid)
    JOIN process USING (upid)
    WHERE process.name = '{args.process}'
    GROUP BY thread.utid
    ORDER BY cpu_pct DESC
    """
    print(f"\n【指定进程 '{args.process}' 的线程 CPU 占用】")
    target_res = tp.query_dict(target_threads_query)
    if not target_res:
        print(f"  未在时间窗口内找到进程 '{args.process}' 或该进程在此期间休眠。")
    else:
        print(f"{'TID':<10} {'CPU %':<10} {'线程名'}")
        for row in target_res:
            print(f"{str(row['tid']):<10} {row['cpu_pct']:<10.2f} {row['thread_name']}")
    print("-" * 50)

if __name__ == "__main__":
    main()
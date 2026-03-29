import argparse
import sys
import os
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

def main():
    parser = argparse.ArgumentParser(description="Perfetto 自动化深度分析工具")
    parser.add_argument("trace_file", help="Trace 文件路径")
    parser.add_argument("--t1", type=float, help="起始时间 (秒)")
    parser.add_argument("--t2", type=float, help="结束时间 (秒)")
    parser.add_argument("--process", type=str, help="目标进程名")
    parser.add_argument("--pid", type=int, help="目标进程PID（与--tid一起使用）")
    parser.add_argument("--tid", type=int, help="目标线程TID（与--pid一起使用）")
    args = parser.parse_args()

    tp_executable = os.path.expanduser('~/.local/share/perfetto/prebuilts/trace_processor_shell')
    config = TraceProcessorConfig(bin_path=tp_executable)
    tp = TraceProcessor(trace=args.trace_file, config=config)

    # 时间边界处理
    bounds = next(tp.query("SELECT start_ts, end_ts FROM trace_bounds"))
    w_start = bounds.start_ts + int(args.t1 * 1e9) if args.t1 else bounds.start_ts
    w_end = bounds.start_ts + int(args.t2 * 1e9) if args.t2 else bounds.end_ts
    w_dur = w_end - w_start

    print(f"分析窗口: {(w_start-bounds.start_ts)/1e9:.3f}s -> {(w_end-bounds.start_ts)/1e9:.3f}s\n")

    # 1. 系统负载 (基于你提供的逻辑，适配窗口)
    sys_sql = f"""
    SELECT 
        (SUM(c_dur) * 100.0) / ({w_dur} * (SELECT COUNT(DISTINCT cpu) FROM cpu)) as load
    FROM (SELECT MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as c_dur 
          FROM sched_slice s JOIN thread t USING(utid) 
          WHERE t.name NOT LIKE 'swapper%' AND s.ts < {w_end} AND s.ts + s.dur > {w_start})
    """
    sys_load = next(tp.query(sys_sql)).load or 0
    print(f"【1. 系统平均负载】 {sys_load:.2f} % (全核平均)\n")

    # 2. Top 10 进程
    print("【2. Top 10 进程 (单核 100% 为基准)】")
    top_proc_sql = f"""
    SELECT 
        p.name, 
        p.pid,
        -- 使用 100.0 强制触发浮点运算，确保不会因为整数除法变成 0
        SUM(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) * 100.0 / {w_dur} as cpu_pct
    FROM sched_slice s 
    JOIN thread t USING(utid) 
    JOIN process p USING(upid)
    WHERE s.ts < {w_end} 
      AND s.ts + s.dur > {w_start} 
      AND p.name NOT LIKE '%swapper%'
    GROUP BY p.upid 
    HAVING cpu_pct > 0  -- 只显示有占用的
    ORDER BY cpu_pct DESC 
    LIMIT 10
    """

    print(f"{'PID':<10} {'CPU %':<10} {'进程名'}")
    for r in tp.query(top_proc_sql):
        print(f"{str(r.pid):<10} {r.cpu_pct:<10.2f} {r.name}")

    # 3. 线程深度分析 
    if args.process:
        print(f"\n【3. 进程 '{args.process}' 线程深度分析 (进程内占比)】")
        thread_sql = f"""
        WITH target_process AS (SELECT upid FROM process WHERE name = '{args.process}' LIMIT 1),
        total_cpu_time AS (
            SELECT SUM(MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start}))) AS total_ns 
            FROM sched_slice WHERE utid IN (SELECT utid FROM thread WHERE upid = (SELECT upid FROM target_process))
            AND ts < {w_end} AND ts + dur > {w_start}
        )
        SELECT 
            t.tid, t.name,
            SUM(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) / 1e6 as cpu_ms,
            (SUM(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) * 100.0 / (SELECT total_ns FROM total_cpu_time)) as inner_pct,
            COUNT(*) as count,
            AVG(MAX(0, MIN(s.ts + s.dur, {w_end}) - MAX(s.ts, {w_start}))) / 1e6 as avg_ms
        FROM sched_slice s JOIN thread t USING(utid)
        WHERE t.upid = (SELECT upid FROM target_process) AND s.ts < {w_end} AND s.ts + s.dur > {w_start}
        GROUP BY t.utid ORDER BY inner_pct DESC LIMIT 15
        """
        res = tp.query(thread_sql)
        print(f"{'TID':<10} {'占比%':<8} {'总耗时ms':<10} {'调度次数':<8} {'平均ms':<8} {'线程名'}")
        for r in res:
            print(f"{str(r.tid):<10} {r.inner_pct:<8.2f} {r.cpu_ms:<10.2f} {r.count:<8} {r.avg_ms:<8.2f} {r.name}")

    # 4. 指定线程的CPU核心分布分析（需要同时提供pid和tid）
    if args.pid is not None and args.tid is not None:
        print(f"\n【4. 线程级CPU核心分布分析 (PID={args.pid}, TID={args.tid})】")

        # 构建线程级CPU分布查询（基于用户提供的SQL示例，适配时间窗口）
        thread_cpu_dist_sql = f"""
        WITH ts AS (
            SELECT *
            FROM sched_slice
            WHERE utid = (
                SELECT utid
                FROM thread
                WHERE tid = {args.tid}
                AND upid = (SELECT upid FROM process WHERE pid = {args.pid})
            )
            AND ts < {w_end} AND ts + dur > {w_start}
        ),
        ts_clipped AS (
            SELECT
                cpu,
                MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as clipped_dur
            FROM ts
        ),
        summ AS (
            SELECT
                cpu,
                SUM(clipped_dur) AS cpu_time_ns
            FROM ts_clipped
            GROUP BY cpu
        ),
        total AS (
            SELECT SUM(cpu_time_ns) AS total_ns FROM summ
        )
        SELECT
            cpu,
            cpu_time_ns / 1e6 AS cpu_time_ms,
            ROUND(cpu_time_ns * 100.0 / (SELECT total_ns FROM total), 2) AS percent
        FROM summ
        ORDER BY cpu
        """

        try:
            thread_cpu_res = list(tp.query(thread_cpu_dist_sql))
            if thread_cpu_res:
                print(f"{'CPU':<6} {'时间(ms)':<12} {'占比%':<10}")
                total_ms = 0
                for r in thread_cpu_res:
                    print(f"{r.cpu:<6} {r.cpu_time_ms:<12.2f} {r.percent:<10.2f}")
                    total_ms += r.cpu_time_ms

                # 获取线程名称
                thread_info_sql = f"""
                SELECT t.name as thread_name, p.name as process_name
                FROM thread t
                JOIN process p ON t.upid = p.upid
                WHERE t.tid = {args.tid} AND p.pid = {args.pid}
                """
                thread_info = list(tp.query(thread_info_sql))
                if thread_info:
                    print(f"\n  线程信息: {thread_info[0].process_name} (PID={args.pid}) -> {thread_info[0].thread_name} (TID={args.tid})")
                    print(f"  总CPU时间: {total_ms:.2f}ms (窗口: {w_dur/1e6:.2f}ms)")
                else:
                    print(f"\n  警告: 未找到PID={args.pid}, TID={args.tid}的线程信息")
            else:
                print(f"  未找到线程 PID={args.pid}, TID={args.tid} 在时间窗口内的调度数据")
        except Exception as e:
            print(f"  线程CPU分布分析失败: {e}")
    elif args.pid is not None or args.tid is not None:
        print("\n  警告: 需要同时提供--pid和--tid参数才能进行线程级CPU分布分析")

if __name__ == "__main__":
    main()
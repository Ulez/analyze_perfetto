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
    parser.add_argument("--tid", type=int, help="目标线程TID（可单独使用，或与--pid/--process一起使用）")
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

    # 3. Top 10 线程CPU占用
    print("\n【3. Top 10 线程CPU占用】")
    top_thread_sql = f"""
    WITH clipped_sched AS (
        SELECT
            utid,
            MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as clipped_dur
        FROM sched_slice
        WHERE ts < {w_end} AND ts + dur > {w_start}
    ),
    thread_stats AS (
        SELECT
            t.tid,
            p.name as process_name,
            t.name as thread_name,
            COUNT(*) as count,
            SUM(cs.clipped_dur) as dur_sum_ns,
            AVG(cs.clipped_dur) as dur_avg_ns
        FROM clipped_sched cs
        JOIN thread t USING(utid)
        JOIN process p USING(upid)
        WHERE t.name NOT LIKE 'swapper%'
        GROUP BY t.utid
        HAVING SUM(cs.clipped_dur) > 0
    ),
    total_sum AS (
        SELECT SUM(dur_sum_ns) as total_ns FROM thread_stats
    )
    SELECT
        tid,
        process_name,
        thread_name,
        count,
        dur_sum_ns / 1e6 as dur_sum_ms,
        CASE
            WHEN (SELECT total_ns FROM total_sum) > 0
            THEN dur_sum_ns * 100.0 / (SELECT total_ns FROM total_sum)
            ELSE 0.0
        END as percentage,
        dur_avg_ns / 1e6 as dur_avg_ms
    FROM thread_stats
    ORDER BY dur_sum_ns DESC
    LIMIT 10
    """

    print(f"{'TID':<10} {'进程名':<20} {'线程名':<20} {'次数':<8} {'总时长(ms)':<15} {'占比%':<12} {'平均时长(ms)':<15}")
    for r in tp.query(top_thread_sql):
        print(f"{str(r.tid):<10} {r.process_name[:18]:<20} {r.thread_name[:18]:<20} {r.count:<8} {r.dur_sum_ms:<15.2f} {r.percentage:<12.2f} {r.dur_avg_ms:<15.2f}")

    # 4. 线程深度分析 
    if args.process:
        print(f"\n【4. 进程 '{args.process}' 线程深度分析 (进程内占比)】")
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

    # 4. 指定线程的CPU核心分布分析（支持pid+tid、process+tid或仅tid三种组合）
    if args.tid is not None:
        # 确定线程定位方式
        locate_by_pid = args.pid is not None
        locate_by_process = args.process is not None
        locate_by_tid_only = not locate_by_pid and not locate_by_process

        if locate_by_pid:
            # 方式1: 使用pid+tid定位
            print(f"\n【5. 线程级CPU核心分布分析 (PID={args.pid}, TID={args.tid})】")

            # 构建线程定位条件
            utid_subquery = f"""
                SELECT utid
                FROM thread
                WHERE tid = {args.tid}
                AND upid = (SELECT upid FROM process WHERE pid = {args.pid})
            """

            # 线程信息查询
            thread_info_sql = f"""
                SELECT t.name as thread_name, p.name as process_name, p.pid
                FROM thread t
                JOIN process p ON t.upid = p.upid
                WHERE t.tid = {args.tid} AND p.pid = {args.pid}
            """

        elif locate_by_process:
            # 方式2: 使用process+tid定位
            print(f"\n【5. 线程级CPU核心分布分析 (进程='{args.process}', TID={args.tid})】")

            # 构建线程定位条件（使用进程名查找）
            utid_subquery = f"""
                SELECT utid
                FROM thread
                WHERE tid = {args.tid}
                AND upid = (SELECT upid FROM process WHERE name = '{args.process}' LIMIT 1)
            """

            # 线程信息查询
            thread_info_sql = f"""
                SELECT t.name as thread_name, p.name as process_name, p.pid
                FROM thread t
                JOIN process p ON t.upid = p.upid
                WHERE t.tid = {args.tid}
                  AND p.upid = (SELECT upid FROM process WHERE name = '{args.process}' LIMIT 1)
            """

        elif locate_by_tid_only:
            # 方式3: 仅使用tid定位（TID在系统中唯一）
            print(f"\n【5. 线程级CPU核心分布分析 (TID={args.tid})】")

            # 构建线程定位条件（仅通过TID，理论上TID唯一）
            utid_subquery = f"""
                SELECT utid
                FROM thread
                WHERE tid = {args.tid}
                LIMIT 1
            """

            # 线程信息查询
            thread_info_sql = f"""
                SELECT t.name as thread_name, p.name as process_name, p.pid
                FROM thread t
                JOIN process p ON t.upid = p.upid
                WHERE t.tid = {args.tid}
                LIMIT 1
            """

        if utid_subquery:
            # 构建线程级CPU分布查询（基于用户提供的SQL示例，适配时间窗口）
            thread_cpu_dist_sql = f"""
            WITH ts AS (
                SELECT *
                FROM sched_slice
                WHERE utid = ({utid_subquery})
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
                    if thread_info_sql:
                        thread_info = list(tp.query(thread_info_sql))
                        if thread_info:
                            if locate_by_pid:
                                pid_info = f"PID={thread_info[0].pid}"
                            elif locate_by_process:
                                pid_info = f"进程='{args.process}'"
                            else:  # locate_by_tid_only
                                pid_info = f"PID={thread_info[0].pid} (通过TID自动匹配)"
                            print(f"\n  线程信息: {thread_info[0].process_name} ({pid_info}) -> {thread_info[0].thread_name} (TID={args.tid})")
                            print(f"  总CPU时间: {total_ms:.2f}ms (窗口: {w_dur/1e6:.2f}ms)")

                            print(f"\n【6. 线程状态分析 (TID={args.tid})】")
                            # 构建线程状态分析查询
                            thread_state_sql = f"""
                            WITH clipped_states AS (
                                SELECT
                                    state,
                                    MAX(0, MIN(ts + dur, {w_end}) - MAX(ts, {w_start})) as clipped_dur
                                FROM thread_state
                                WHERE utid = ({utid_subquery})
                                AND ts < {w_end} AND ts + dur > {w_start}
                            ),
                            state_summary AS (
                                SELECT
                                    state,
                                    SUM(clipped_dur) as total_dur_ns,
                                    COUNT(*) as occurrences
                                FROM clipped_states
                                GROUP BY state
                            ),
                            total AS (
                                SELECT SUM(total_dur_ns) as total_ns FROM state_summary
                            )
                            SELECT
                                state,
                                total_dur_ns / 1e6 as total_dur_ms,
                                occurrences,
                                ROUND(total_dur_ns * 100.0 / (SELECT total_ns FROM total), 2) as percentage
                            FROM state_summary
                            ORDER BY total_dur_ns DESC
                            """

                            try:
                                state_results = list(tp.query(thread_state_sql))
                                if state_results:
                                    print(f"{'状态':<8} {'时间(ms)':<12} {'占比%':<10} {'次数':<8}")
                                    for r in state_results:
                                        print(f"{r.state:<8} {r.total_dur_ms:<12.2f} {r.percentage:<10.2f} {r.occurrences:<8}")

                                    # 显示总计
                                    total_state_ms = sum(r.total_dur_ms for r in state_results)
                                    print(f"\n  状态总时间: {total_state_ms:.2f}ms (窗口: {w_dur/1e6:.2f}ms)")
                                else:
                                    print("  未找到线程状态数据（可能thread_state表不存在或该时间段内无状态数据）")
                            except Exception as e:
                                print(f"  线程状态分析失败: {e}")
                        else:
                            if locate_by_pid:
                                print(f"\n  警告: 未找到PID={args.pid}, TID={args.tid}的线程信息")
                            elif locate_by_process:
                                print(f"\n  警告: 未找到进程'{args.process}'中TID={args.tid}的线程信息")
                            else:  # locate_by_tid_only
                                print(f"\n  警告: 未找到TID={args.tid}的线程信息")
                else:
                    if locate_by_pid:
                        print(f"  未找到线程 PID={args.pid}, TID={args.tid} 在时间窗口内的调度数据")
                    elif locate_by_process:
                        print(f"  未找到进程'{args.process}'中TID={args.tid}在时间窗口内的调度数据")
                    else:  # locate_by_tid_only
                        print(f"  未找到线程 TID={args.tid} 在时间窗口内的调度数据")
            except Exception as e:
                print(f"  线程CPU分布分析失败: {e}")

    elif args.pid is not None:
        # 只提供了pid，没有提供tid
        print("\n  警告: 使用--pid参数时需要同时指定线程TID，请使用--tid参数")
        print("  例如: --pid 2244 --tid 2446  或仅使用 --tid 2446")

if __name__ == "__main__":
    main()
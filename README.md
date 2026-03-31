# Perfetto 自动化深度分析工具

一个基于Python的Perfetto Trace分析工具，提供系统级、进程级和线程级的深度性能分析。

## 功能特性

1. **系统负载分析** - 计算指定时间窗口内的系统平均CPU负载
2. **Top 10 进程分析** - 显示CPU占用最高的进程及其百分比
3. **Top 10 线程CPU占用** - 显示CPU占用最高的线程及其详细统计
4. **进程线程深度分析** - 分析指定进程内各线程的CPU占用分布
5. **线程级CPU核心分布** - 分析指定线程在不同CPU核心上的运行分布
6. **线程状态分析** - 分析指定线程的运行状态（Running、Runnable、D等）占比
7. **线程优先级分析** - 显示指定线程的平均调度优先级
8. **线程阻塞函数分析** - 分析D状态（Uninterruptible Sleep）线程的阻塞函数及最长阻塞时长

## 安装要求

### 1. Python环境
- Python 3.7+
- 建议使用虚拟环境

### 2. 依赖安装
```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# 安装依赖
pip install perfetto pandas numpy plotille
```

### 3. Perfetto Trace Processor配置
本工具依赖 `trace_processor_shell` 来解析Perfetto trace文件。`perfetto` Python包会自动下载并管理此工具。

#### 自动下载（推荐）
首次运行 `ap.py` 时，`perfetto` 库会自动从Google服务器下载对应平台的 `trace_processor_shell`，保存到：
- **Linux/macOS**: `~/.local/share/perfetto/prebuilts/trace_processor_shell`
- **Windows**: `%LOCALAPPDATA%\perfetto\prebuilts\trace_processor_shell.exe`

**注意**：自动下载需要网络连接。如果遇到网络问题，请参考手动安装方法。

#### 手动安装（离线环境或网络问题）
1. 访问 [Perfetto Releases页面](https://github.com/google/perfetto/releases)
2. 下载对应平台的 `trace_processor_shell`：
   - **Linux**: `trace_processor_shell-linux`
   - **macOS**: `trace_processor_shell-darwin`
   - **Windows**: `trace_processor_shell-windows.exe`
3. 重命名为 `trace_processor_shell`（Windows为 `trace_processor_shell.exe`）
4. 放置到正确目录：
   ```bash
   # Linux/macOS
   mkdir -p ~/.local/share/perfetto/prebuilts/
   mv trace_processor_shell ~/.local/share/perfetto/prebuilts/
   chmod +x ~/.local/share/perfetto/prebuilts/trace_processor_shell

   # Windows
   mkdir %LOCALAPPDATA%\perfetto\prebuilts
   move trace_processor_shell-windows.exe %LOCALAPPDATA%\perfetto\prebuilts\trace_processor_shell.exe
   ```

#### 配置验证
完成手动安装后，可以通过以下方式验证配置：

```bash
# 1. 检查文件是否存在并具有可执行权限
ls -la ~/.local/share/perfetto/prebuilts/trace_processor_shell
# 应该显示类似：-rwxr-xr-x  1 user  staff  ... trace_processor_shell

# 2. 测试Perfetto库导入
python -c "from perfetto.trace_processor import TraceProcessor; print('Perfetto导入成功')"

# 3. 运行简单trace分析测试（可选）
python ap.py --help
```

如果文件已正确放置但工具仍无法运行，请检查：
1. **文件权限**：确保 `chmod +x` 已执行
2. **文件路径**：确认文件在正确目录且文件名正确
3. **平台匹配**：下载的版本是否与操作系统匹配

#### 使用自定义路径（高级）
如需使用非标准路径的 `trace_processor_shell`，可以修改 `ap.py` 第17行：
```python
# 默认路径
tp_executable = os.path.expanduser('~/.local/share/perfetto/prebuilts/trace_processor_shell')

# 修改为自定义路径，例如：
# tp_executable = '/path/to/your/trace_processor_shell'
```

#### 常见安装问题
1. **网络连接失败**：手动下载并放置到正确目录
2. **权限问题**：确保 `trace_processor_shell` 有可执行权限
3. **版本不匹配**：确保 `perfetto` Python包与 `trace_processor_shell` 版本兼容
4. **文件不存在**：检查路径和文件名是否正确
5. **平台不匹配**：下载的版本是否与操作系统匹配（如macOS下载darwin版本）

## 使用方法

### 基本语法
```bash
python ap.py <trace_file> [选项]
```

### 参数说明
| 参数 | 说明 | 示例 |
|------|------|------|
| `trace_file` | Perfetto trace文件路径（必需） | `2026-03-29_15-16-e20ee0.pftrace` |
| `--t1` | 起始时间（秒，相对于trace开始） | `--t1 2.0` |
| `--t2` | 结束时间（秒，相对于trace开始） | `--t2 5.5` |
| `--process` | 目标进程名（用于线程深度分析） | `--process surfaceflinger` |
| `--pid` | 目标进程PID（需与`--tid`一起使用） | `--pid 2244` |
| `--tid` | 目标线程TID（可单独使用，或与`--pid`/`--process`一起使用） | `--tid 2446` |

### 使用示例

#### 示例1：基础分析（全时间窗口）
```bash
python ap.py 2026-03-29_15-16-e20ee0.pftrace
```
输出：
1. 系统平均负载
2. Top 10 进程
3. Top 10 线程CPU占用

#### 示例2：指定时间窗口分析
```bash
python ap.py 2026-03-29_15-16-e20ee0.pftrace --t1 2.0 --t2 5.5
```
分析2.0秒到5.5秒时间窗口内的性能数据。

#### 示例3：进程级线程分析
```bash
python ap.py 2026-03-29_15-16-e20ee0.pftrace --process surfaceflinger
```
输出指定进程内各线程的CPU占用分布（进程内占比）。

#### 示例4：线程级详细分析（三种定位方式）
```bash
# 方式1：仅使用TID（TID在系统中唯一）
python ap.py 2026-03-29_15-16-e20ee0.pftrace --tid 2446

# 方式2：使用进程名+TID
python ap.py 2026-03-29_15-16-e20ee0.pftrace --process surfaceflinger --tid 2446

# 方式3：使用PID+TID
python ap.py 2026-03-29_15-16-e20ee0.pftrace --pid 2244 --tid 2446
```
输出：
1. 线程级CPU核心分布（各CPU核心的占用情况）
2. 线程状态分析（Running、Runnable、D等状态占比）
3. 线程优先级信息（平均调度优先级）
4. 线程阻塞函数分析（当线程存在D状态时自动分析阻塞函数及最长阻塞时长）

## 输出说明

### 1. 系统平均负载
```
【1. 系统平均负载】 45.32 % (全核平均)
```
- **含义**：所有CPU核心的平均利用率
- **计算方式**：非swapper线程的总CPU时间 / (时间窗口 × CPU核心数)

### 2. Top 10 进程
```
【2. Top 10 进程 (单核 100% 为基准)】
PID        CPU %      进程名
2244       12.34       surfaceflinger
```
- **CPU %**：进程在时间窗口内的CPU占用百分比
- **基准**：单核100%为基准（例如12.34%表示占用单核的12.34%）

### 3. Top 10 线程CPU占用
```
【3. Top 10 线程CPU占用】
TID        进程名                线程名                次数      总时长(ms)      占比%        平均时长(ms)
2446       surfaceflinger        Binder:2244_1        2350      451.33         4.85         0.19
```
- **次数**：调度次数
- **总时长(ms)**：CPU总占用时间（毫秒）
- **占比%**：占所有非swapper线程总CPU时间的百分比
- **平均时长(ms)**：每次调度的平均CPU时间（毫秒）

### 4. 进程线程深度分析
```
【4. 进程 'surfaceflinger' 线程深度分析 (进程内占比)】
TID        占比%      总耗时ms    调度次数    平均ms      线程名
2446       45.23      123.45      2350        0.05       Binder:2244_1
```
- **占比%**：线程在进程内的CPU占用比例
- **总耗时ms**：线程总CPU时间（毫秒）
- **调度次数**：线程被调度的次数

### 5. 线程级CPU核心分布
```
【5. 线程级CPU核心分布分析 (TID=2446)】
CPU     时间(ms)      占比%
0       45.23         23.45
1       32.12         16.67
```
- **CPU**：CPU核心编号
- **时间(ms)**：在该核心上的运行时间（毫秒）
- **占比%**：占该线程总CPU时间的比例

### 6. 线程状态分析
```
【6. 线程状态分析 (TID=2446)】
状态      时间(ms)      占比%       次数
R         123.45       45.23       2350
S         45.67        16.78       1200
```
- **状态**：线程状态（R=Running, S=Sleeping, D=Uninterruptible Sleep等）
- **时间(ms)**：处于该状态的总时间（毫秒）
- **占比%**：占线程总状态时间的比例
- **次数**：进入该状态的次数

### 7. 线程优先级信息
```
  线程信息: surfaceflinger (PID=2244) -> Binder:2244_1 (TID=2446, 优先级: 120)
```
- **优先级**：线程的平均调度优先级（数值越小优先级越高，Linux默认120为普通优先级）
- **显示位置**：在线程级分析中自动显示，位于CPU核心分布和状态分析之前
- **数据源**：从`sched_slice`表的`priority`字段计算平均值

### 8. 线程阻塞函数分析
```
【7. 线程阻塞函数分析 (TID=19282)】
阻塞函数                     时间(ms)    占比%     次数     平均(µs)    最长(µs)
folio_wait_bit_common        324.25     87.45     2765     117.27      1292.71
mmap_read_lock_killable      45.49      12.27     3        15164.38    18949.06
```
- **阻塞函数**：导致线程进入D状态（Uninterruptible Sleep）的内核函数
- **时间(ms)**：在该阻塞函数上花费的总时间（毫秒）
- **占比%**：占所有D状态时间的比例
- **次数**：进入该阻塞函数的次数
- **平均(µs)**：每次阻塞的平均时长（微秒）
- **最长(µs)**：单次最长阻塞时长（微秒）
- **触发条件**：仅当线程存在D状态且`thread_state`表包含`blocked_function`字段时自动分析

## 技术细节

### 时间窗口处理
工具使用精确的时间窗口裁剪算法：
```sql
MAX(0, MIN(ts + dur, w_end) - MAX(ts, w_start))
```
- 正确处理部分重叠的调度片段
- 避免遗漏边界上的调度事件

### 数据源
- 主要数据表：`sched_slice`（调度片段）
- 辅助表：`thread_state`（线程状态）、`thread`、`process`、`cpu`
- 时间参考：`trace_bounds`（trace时间范围）

### 线程定位方式
1. **仅TID**：TID在Linux系统中全局唯一
2. **进程名+TID**：通过进程名定位进程，再查找指定TID
3. **PID+TID**：通过PID定位进程，再查找指定TID

## 注意事项

1. **Perfetto Trace Processor**：首次运行时会自动下载`trace_processor_shell`，请确保网络连接正常
2. **时间单位**：所有时间显示均使用毫秒（ms）或纳秒（ns），1秒=1000毫秒=1,000,000,000纳秒
3. **线程状态表**：某些trace文件可能不包含`thread_state`表，此时线程状态分析和阻塞函数分析会显示相应提示。即使有`thread_state`表，也可能不包含`blocked_function`字段
4. **性能考虑**：分析大型trace文件时，建议指定时间窗口以减少内存使用

## 常见问题

### Q: 运行时出现"无法下载trace_processor_shell"错误怎么办？
A: 这通常是由于网络连接问题导致无法从Google服务器下载。请尝试：
1. **手动下载**：参考[Perfetto Trace Processor配置](#3-perfetto-trace-processor配置)部分的手动安装方法
2. **检查网络**：确保可以访问 `https://github.com/google/perfetto/releases`
3. **使用代理**：如有需要，配置合适的网络代理
4. **验证权限**：确保目标目录有写入权限

### Q: 为什么Top 10进程的结果与其他工具不同？
A: 本工具使用精确的时间窗口裁剪算法，计算调度片段在指定时间窗口内的重叠部分，而其他工具可能只计算完全在窗口内的片段或使用不同的分母。

### Q: 如何确定线程的TID？
A: 可以通过以下方式获取TID：
1. 使用本工具的Top 10线程分析查看TID
2. 使用Perfetto UI查看线程信息
3. 通过系统工具如`ps -T`或`top -H`查看

### Q: 线程状态分析显示"未找到线程状态数据"怎么办？
A: 这可能是因为trace文件没有记录线程状态信息。请确保在录制trace时启用了线程状态跟踪。

### Q: 为什么阻塞函数分析没有显示或显示不完整？
A: 阻塞函数分析需要满足以下条件：
1. trace文件包含`thread_state`表
2. `thread_state`表包含`blocked_function`字段
3. 线程在指定时间窗口内存在D状态（Uninterruptible Sleep）
如果以上条件不满足，阻塞函数分析可能不会显示或显示"未找到阻塞函数数据"。

## 更新日志

### v1.1.0 (2026-03-30)
- 新增线程优先级分析功能
- 新增线程阻塞函数分析功能（支持最长阻塞时长）
- 优化所有表格输出对齐（左对齐格式）
- 完善线程状态分析，自动处理D状态阻塞函数

### v1.0.0 (2026-03-29)
- 初始版本发布
- 支持系统负载、进程、线程三级分析
- 支持时间窗口、进程名、PID、TID等多种参数组合
- 自动下载并配置Perfetto Trace Processor
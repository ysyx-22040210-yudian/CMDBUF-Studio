// 严格约束随机：所有未被协议/事务历史限定的输入由 VCS 随机求解。
// 用户通常只改下面三个 constraint 块中的值；不需要写 driver/monitor/scoreboard。
// 约束赋值写 ==，不是 =。所有信号名与 DUT 输入端口一致。
// 已包含：变量声明、E03/E04/E05/E06、MEM N+4、收尾；见 sv_tc_base.svh。
// cycle 从 0 起算且包含复位/停钟槽；edge_count 只数真实上升沿。
// arready / req / arb_addr / mem_due / due_data / due_error 是只读上下文。
class sv_user_values extends cmdbuf_sv_tc;
    // 运行长度：soft 允许 Make CYCLES=N 覆盖。用户激励结束后自动排空。
    constraint c_run { if (stage == 2) soft num_cycles == 2000; }

    // 编辑区 1：每槽先求解时钟、复位、rready，再读取稳定的 DUT 反馈。
    // clk_enable=0 保持 clk 为低；low/high 是 ns，可改变周期、占空比和停钟时长。
    constraint c_control {
        if (stage == 0 && phase == 0) {
            rst_n == (cycle >= 3);
            clk_enable == 1;
            clk_low_ns == 5; clk_high_ns == 5;
            // rready 不固定：在 000..111 范围内随机。
        }
    }

    // 编辑区 2：在上述控制量固定后，与项目硬约束一起求解其余输入。
    // 不写约束的变量保留随机性；冲突会停止并报 cycle/edge/反馈上下文。
    // issue_data/issue_error 在 ISSUE 成功时预约给 N+4；不是 DUT 端口。
    constraint c_inputs {
        if (stage == 1 && phase == 0) {
            // 例如只随机 XAU0/1：if (arvalid) src inside {0,1};
            if (rst_n && arvalid) {
                src inside {0,2}; araddr inside {[18'h12000:18'h121ff]};
                arid == 8'h5a; ar_port_id == 17; arresp == 3;
            }
            issue_data == 128'h77770000111122223333444455556666;
            issue_error == 1;
        }
    }
endclass

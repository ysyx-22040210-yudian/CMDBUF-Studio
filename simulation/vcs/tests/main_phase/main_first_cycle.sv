// 启动时序正例：cycle=0 准备请求，默认 10 ns 驱动并在 20 ns 接受。
// 用户只需修改 main_phase 的赋值和 if 条件；没有 constraint/randomize。
// 每次 @(posedge clk) 后执行一组带条件赋值，供下一上升沿采样。
class main_first_cycle extends cmdbuf_main_tc;
    function new(); num_cycles=4; endfunction

    virtual task main_phase;
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            // begin_cycle 等待本沿 NBA 完成。
            begin_cycle(cycle);

            // 编辑区 A：复位保持 1；时钟单位 ns，pause 延长下一段低电平。
            rst_n = 1; // Environment: low at t=0, released at #1 (1 ns).
            rready = 3'b111;
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            // sample_context 等待同一时间点的反馈稳定。
            sample_context();

            // 编辑区 B：选中的 src 必须 ready，空闲拍使用 src=3。
            if (!rst_n || arready[0] !== 1'b1)
                arvalid = 0;
            else if (cycle == 0)
                arvalid = 1;
            else
                arvalid = 0;
            if (arvalid) begin
                src = 0;       // E03：有效请求只填写 0/1/2。
                arresp = 0;    // E04：有效请求只填写 0 或 3。
            end else begin
                src = 3; arresp = 0;
            end
            araddr = 18'h12001;
            arid = 8'h2a; ar_port_id = 5'd6;
            rw_rd_gnt = 8'hff;

            // E06：8 个 Bank 的所选半区，成功发射时必须 busy。
            for (int b=0; b<8; b++) begin
                if (req[b]) ar_rd_gnt[b] = 1;
                else ar_rd_gnt[b] = 0;
                if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b])
                    rd_mem_status[b] = (2'b01 << arb_addr[0]);
                else
                    rd_mem_status[b] = 0;
            end

            // 本拍若 ISSUE 成功，预约 N+4 的返回数据。用户在此改数据/错误位。
            issue_data = 128'h0123456789abcdef_fedcba9876543210;
            issue_error = 0;
            // MEM 有返回任务时按历史驱动；无任务时可固定值或随机。
            if (mem_due) begin
                mem_rdata = due_data;
                pc_err = due_error;
            end else begin
                mem_rdata = 0; pc_err = 0;
            end
            drive_inputs(); // 只校验并原样驱动，不在框架里改写 TC 的值。
        end
    endtask
endclass

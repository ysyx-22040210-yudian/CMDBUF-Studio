// 过程式入门 TC：main_phase 中每上升沿后赋值，第 3 次赋值发送一条请求。
// 用户只需修改 main_phase 的赋值和 if 条件；没有 constraint/randomize。
// 每次 @(posedge clk) 后执行一组带条件赋值，供下一上升沿采样。
class main_bad_busy extends cmdbuf_main_tc;
    function new(); num_cycles=64; endfunction

    virtual task main_phase;
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            #1; // DUT 本拍的 NBA 更新完成后再驱动，避免采样竞争。
            begin_cycle(cycle);

            // 编辑区 A：时钟、复位、响应接收。单位 ns；pause 延长下一段低电平。
            rst_n = (cycle >= 3);
            rready = 3'b111;
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            #1; // rst_n/rready 影响组合反馈，先等待稳定，再读取。
            sample_context();

            // 编辑区 B：AR 输入。E05 必须显式处理 ready 全零的分支。
            if (!rst_n || arready == 3'b000)
                arvalid = 0;
            else if (cycle == 3)
                arvalid = 1;
            else
                arvalid = 0;
            if (arvalid) begin
                src = 0;       // E03：有效请求只填写 0/1/2。
                arresp = 0;    // E04：有效请求只填写 0 或 3。
            end else begin
                src = 0; arresp = 0;
            end
            araddr = 18'h12001;
            arid = 8'h2a; ar_port_id = 5'd6;
            ar_rd_gnt = 8'hff; rw_rd_gnt = 8'hff;

            // E06：8 个 Bank 的所选半区，成功发射时必须 busy。
            for (int b=0; b<8; b++) begin
                if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b])
                    rd_mem_status[b] = 0;
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

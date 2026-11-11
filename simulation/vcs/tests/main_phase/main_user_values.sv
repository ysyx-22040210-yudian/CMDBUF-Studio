// 过程式合法随机：在每个 if 分支内按当前合法取值范围生成随机数。
// 用户只需修改 main_phase 的赋值和 if 条件；没有 constraint/randomize。
// 每次 @(posedge clk) 后执行一组带条件赋值，供下一上升沿采样。
class main_user_values extends cmdbuf_main_tc;
    function new(); num_cycles=2000; endfunction

    virtual task main_phase;
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            #1; // DUT 本拍的 NBA 更新完成后再驱动，避免采样竞争。
            begin_cycle(cycle);

            // 编辑区 A：时钟、复位、响应接收。单位 ns；pause 延长下一段低电平。
            rst_n = (cycle >= 3);
            rready = $urandom_range(7,0);
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            #1; // rst_n/rready 影响组合反馈，先等待稳定，再读取。
            sample_context();

            // 编辑区 B：AR 输入。E05 必须显式处理 ready 全零的分支。
            if (!rst_n || arready == 3'b000)
                arvalid = 0;
            else
                arvalid = $urandom_range(1,0);
            if (arvalid) begin
                if ($urandom_range(1,0)) src = 2; else src = 0; // 只在合法的 0/1/2 中生成。
                arresp = 3;
            end else begin
                src = $urandom_range(3,0);
                arresp = $urandom_range(3,0);
            end
            araddr = $urandom_range(18'h121ff,18'h12000);
            arid = 8'h5a; ar_port_id = 17;
            ar_rd_gnt = $urandom_range(255,0);
            rw_rd_gnt = $urandom_range(255,0);

            // E06：8 个 Bank 的所选半区，成功发射时必须 busy。
            for (int b=0; b<8; b++) begin
                for (int h=0; h<2; h++) begin
                    if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b] && h==arb_addr[0])
                        rd_mem_status[b][h] = 1;
                    else
                        rd_mem_status[b][h] = $urandom_range(1,0);
                end
            end

            // 本拍若 ISSUE 成功，预约 N+4 的返回数据。用户在此改数据/错误位。
            issue_data = {$urandom(),$urandom(),$urandom(),$urandom()};
            issue_error = $urandom_range(1,0);
            // MEM 有返回任务时按历史驱动；无任务时可固定值或随机。
            if (mem_due) begin
                mem_rdata = due_data;
                pc_err = due_error;
            end else begin
                mem_rdata = {$urandom(),$urandom(),$urandom(),$urandom()};
                pc_err = $urandom_range(1,0);
            end
            drive_inputs(); // 只校验并原样驱动，不在框架里改写 TC 的值。
        end
    endtask
endclass

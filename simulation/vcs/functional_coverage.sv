// Edit these properties in Studio's 功能覆盖率 page or in this file.
// All properties sample at the rising edge, before DUT NBA updates.
// Available helpers: ar_fire, issue_fire, r_fire, past_valid.
//
// Examples of optional checks:
// E_AR_ID: assume property (@(posedge clk) disable iff (!rst_n)
//     arvalid |-> arid != 8'hff);
// A_PENDING: assert property (@(posedge clk) disable iff (!rst_n)
//     ar_fire && !issue_fire |=> pending_cnt == $past(pending_cnt) + 4'd1);

C_XAU0_AR: cover property (@(posedge clk) disable iff (!rst_n) ar_fire && src == 2'd0);
C_XAU1_AR: cover property (@(posedge clk) disable iff (!rst_n) ar_fire && src == 2'd1);
C_XAU2_AR: cover property (@(posedge clk) disable iff (!rst_n) ar_fire && src == 2'd2);
C_PARITY_R: cover property (@(posedge clk) disable iff (!rst_n) r_fire && rresp == 2'b10);

// Test fixture: a small clocked 8x8 multiply-accumulate (standalone Verilog).
// The multiply+add in the combinational path gives a realistic (sub-max)
// Fmax so the timing numbers we scrape are actually interesting.
module mac #(
    parameter W = 8
) (
    input  wire           clk,
    input  wire           rst,
    input  wire [W-1:0]   a,
    input  wire [W-1:0]   b,
    output reg  [2*W+3:0] acc
);
    always @(posedge clk) begin
        if (rst)
            acc <= 0;
        else
            acc <= acc + a * b;
    end
endmodule

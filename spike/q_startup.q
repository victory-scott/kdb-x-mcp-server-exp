/ Spike q process: SQL module + tiny test data
use`kx.sql
trades:([] sym:`AAPL`MSFT`GOOG`AAPL`AMZN; px:1.5 2.5 3.5 4.5 5.5; sz:100 200 300 400 500; ts:.z.p+til 5)
quotes:([] sym:`AAPL`MSFT`GOOG; bid:1.0 2.0 3.0; ask:1.1 2.1 3.1)
-1"spike q ready: trades, quotes, .s loaded";
\

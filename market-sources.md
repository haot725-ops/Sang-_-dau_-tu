# Nguồn danh sách mã thị trường

Snapshot tra cứu ngày 24/11/2026 (giờ Việt Nam). Phạm vi dự kiến của Sàng là cổ phiếu phổ thông trên HOSE, HNX và UPCoM; không gộp chứng quyền, ETF/chứng chỉ quỹ hay trái phiếu.

Snapshot được đóng gói trong `universe.json` với các trường `t` (mã), `n` (tên), `x` (sàn), `i` (ngành nếu nguồn có), cùng `as_of`, `sources` và tổng `count`. Đây là danh sách tra cứu, không phải danh sách mã đã có giá hoặc báo cáo tài chính.

## HOSE

Nguồn chính thức: [Danh sách cổ phiếu HOSE](https://www.hsx.vn/en/quan-ly-niem-yet/co-phieu). HOSE đặt cổ phiếu thành mục riêng, tách khỏi chứng chỉ quỹ, ETF và covered warrants. Trang đang chạy bằng ứng dụng JavaScript; tại thời điểm tra cứu không xác nhận được file CSV/XLS hay API tĩnh có thể tải đầy đủ. Trang không ghi ngày hiệu lực của toàn danh sách.

Nguồn phụ để có snapshot mã máy đọc được: [StockAnalysis — Ho Chi Minh Stock Exchange Stocks](https://stockanalysis.com/list/ho-chi-minh-stock-exchange/). Trang tự mô tả đây là danh sách mã cổ phiếu đang giao dịch tích cực, cập nhật hằng ngày; hiển thị 412 mã khi truy cập ngày 24/11/2026. Đây không phải nguồn của sở giao dịch và phạm vi “actively traded” có thể khác toàn bộ cổ phiếu còn niêm yết, ví dụ mã tạm ngừng hoặc ít giao dịch. Vì vậy không ghi 412 như một con số chính thức của HOSE.

Các trang HOSE riêng cho nhóm khác: [chứng chỉ quỹ](https://www.hsx.vn/en/quan-ly-niem-yet/chung-chi-quy), [ETF](https://www.hsx.vn/en/quan-ly-niem-yet/etf), [covered warrants](https://www.hsx.vn/en/quan-ly-niem-yet/cw).

## HNX

Nguồn chính thức: [Danh sách cổ phiếu niêm yết HNX](https://hnx.vn/en-gb/cophieu-etfs/chung-khoan-ny.html). Trang gửi POST tới [endpoint của HNX](https://hnx.vn/ModuleIssuer/List/ListSearch_Datas); phản hồi JSON bọc HTML bảng, có thể phân trang bằng `p_currentpage` và `p_record_on_page`. Phản hồi đã kiểm tra ngày 24/11/2026 ghi 299 mã cổ phiếu niêm yết. Endpoint không phải API JSON được tài liệu hóa và trang không ghi ngày hiệu lực của snapshot.

## UPCoM

Nguồn chính thức: [Danh sách chứng khoán UPCoM của HNX](https://hnx.vn/en-gb/cophieu-etfs/chung-khoan-uc.html). Trang gửi POST tới [endpoint UPCoM của HNX](https://hnx.vn/ModuleIssuer/UC_Issuer/ListSearch_Datas), với `p_market_code=UC`; bảng gắn nhóm dưới mục cổ phiếu UPCoM và có cột khối lượng cổ phiếu đăng ký. Phản hồi kiểm tra ngày 24/11/2026 ghi 807 mã và trích xuất đủ 807 hàng duy nhất. Endpoint trả JSON bọc HTML, không phải API JSON có cấu trúc được tài liệu hóa; không hiển thị ngày hiệu lực tổng thể.

## Cách đọc snapshot

Snapshot gồm 1.518 mã duy nhất: 412 HOSE, 299 HNX và 807 UPCoM; 57 mã cấu hình cũ đều có mặt. Danh sách này là universe để tìm và chọn mã, không có nghĩa dữ liệu BCTC hay giá của từng mã đã được tải. `tickers.json` tiếp tục là nhóm 57 mã crawler tự động cập nhật; mã khác được nạp khi chọn, nếu Worker hoạt động. Snapshot HOSE dùng nguồn phụ, “đang giao dịch tích cực”, và không bảo đảm bao phủ cổ phiếu ít giao dịch/tạm ngừng; HNX/UPCoM lấy từ danh sách chính thức nhưng không xác nhận giao dịch đang diễn ra trong ngày.

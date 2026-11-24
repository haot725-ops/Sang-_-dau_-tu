# Sàng – hướng dẫn đưa lên GitHub

App 3 bước (Tầm soát → Định giá → Kỹ thuật), dùng báo cáo tài chính CafeF, lịch sử giá và quote tham khảo qua Worker. Universe tra cứu nằm trong `universe.json`; hiện có 1.518 mã snapshot ngày 24/11/2026. Phạm vi dự kiến là cổ phiếu phổ thông HOSE, HNX và UPCoM; không gồm chứng quyền, ETF/chứng chỉ quỹ hoặc trái phiếu. Chi tiết nguồn và giới hạn nằm trong `market-sources.md`.

## 1. Đưa file lên repo (GitHub Pages)

Upload toàn bộ file/thư mục, giữ nguyên cấu trúc, gồm `index.html`, `sw.js`, `universe.json`, `market-sources.md`, `manifest.json`, `icons/`, `fetch_data.py`, `cafef_fetch.py`, `tickers.json`, `worker.js` và `.github/workflows/cafef.yml`.

Settings → Pages → Deploy from branch → `main` / root. Thư mục `.github` bắt đầu bằng dấu chấm; có thể upload cả thư mục hoặc tạo file workflow thủ công.

## 2. Dữ liệu định kỳ và mã ngoài nhóm hiện có

Tab Actions → “Cập nhật số liệu (báo cáo CafeF và giá)” → Run workflow để tạo/cập nhật `data/`. Lịch tự chạy lúc 16:40 giờ Việt Nam từ thứ Hai đến thứ Sáu. Lịch giữ nguyên việc lấy báo cáo và giá cho 57 mã cốt lõi; đồng thời mỗi lần chạy xử lý thêm một lô BCTC luân phiên 200 mã ngoài nhóm đó. Batch dùng tối đa 3 worker cho 3 mã khác nhau; mỗi mã vẫn gọi lần lượt ba API CafeF và giữ nghỉ 1 giây giữa các báo cáo, cùng retry/backoff khi lỗi. Cách này giảm thời gian chờ tổng thể mà vẫn giới hạn mức đồng thời tới nguồn; thời gian thực tế còn tùy phản hồi và retry. 1.461 mã còn lại được quét đủ trong khoảng 8 ngày làm việc (thường 1,5–2 tuần). Tối đa 50 mã lỗi ở lô trước được ưu tiên thử lại trong lần chạy sau. Lịch sử giá định kỳ vẫn chỉ cập nhật cho nhóm 57 mã. Xem trạng thái batch, lỗi và con trỏ ở `data/status.json` và `data/state/`.

`tickers.json` vẫn là nhóm 57 mã dùng cho báo cáo và giá định kỳ; các mã ngoài nhóm được bổ sung tự động theo lô cho BCTC, không quét lịch sử giá định kỳ. Lô được chọn từ `universe.json` và con trỏ `data/state/universe_cursor.json` được lưu cùng dữ liệu để các lần chạy tiếp tục tuần tự. Nút `universe_batch` trong Run workflow cho phép chạy thêm một lô ngay khi chạy thủ công. App đọc trước tối đa 300 hồ sơ lúc khởi động để nhẹ trên điện thoại; khi chọn mã khác, app tải hồ sơ mới nhất đã lưu trong repo theo mã đó rồi mới dùng Worker làm phương án dự phòng. Khi nguồn chặn hoặc lỗi, mã được ghi vào hàng đợi retry; dữ liệu cũ được giữ nguyên.

## 3. Worker trung gian (cần cho mã ngoài dữ liệu định kỳ và quote)

Cloudflare → Workers → Create → dán nội dung `worker.js` → Deploy. Nếu địa chỉ GitHub Pages khác `https://haot725-ops.github.io`, cập nhật dòng `ALLOW` trong `worker.js`, rồi deploy lại. Trong app vào “Dữ liệu” → “Worker trung gian”, dán URL `https://...workers.dev` và lưu.

Worker hỗ trợ BCTC CafeF, lịch sử nến ngày và quote VPS. Quote chỉ gọi các mã đã có lịch sử giá trong app và tối đa 80 mã mỗi lần; không yêu cầu quote cho toàn bộ 1.518 mã.

## 4. Biểu đồ và độ cập nhật

Trong bước “Kỹ thuật”, chọn khung nến “Ngày”, “Tuần” hoặc “Tháng”. Tuần và tháng được gộp từ OHLCV nến ngày: mở cửa đầu kỳ, cao nhất, thấp nhất, đóng cửa cuối kỳ và tổng khối lượng. Chỉ báo MA, RSI và MACD được tính lại trên chuỗi nến của khung đã chọn. Các nút `3M`, `6M`, `1Y` là khoảng hiển thị; chúng độc lập với khung nến.

Biểu đồ không chạy theo thời gian thực: nguồn lịch sử cung cấp nến ngày. Quote VPS là giá tham khảo gần thời gian thực, qua Worker có cache tối đa 15 giây; timestamp trong app cho biết thời điểm Worker lấy/trả phản hồi, không xác nhận timestamp gốc hoặc độ trễ thật của nhà cung cấp. Vì thế giá quote có thể mới hơn nến đóng cửa gần nhất, nhưng không làm cho biểu đồ thành nến realtime.

Với khung tháng, dữ liệu lịch sử hiện lưu tối đa khoảng 260 phiên ngày, tương đương khoảng 12 nến tháng. Số nến đó chưa đủ để tính MA50 tháng; app sẽ nêu giới hạn này.

## Giới hạn cần biết

- Universe 1.518 mã không đồng nghĩa từng mã đã có BCTC, lịch sử giá hoặc quote. Snapshot HOSE lấy từ nguồn thứ cấp StockAnalysis với phạm vi “đang giao dịch tích cực”, không bảo đảm đầy đủ tất cả mã còn niêm yết. HNX và UPCoM lấy từ bảng chính thức HNX tại ngày truy xuất; số liệu có thể thay đổi.
- Báo cáo tài chính dùng API công khai nhưng không chính thức của CafeF. Bộ hiện có đã được đối chiếu với API CafeF cho 57 mã ngày 24/11/2026; khớp kỳ, mã chỉ tiêu và giá trị. Đây là kiểm tra nhất quán với CafeF, chưa phải đối chiếu độc lập với báo cáo gốc doanh nghiệp công bố.
- API và nguồn giá có thể thay đổi hoặc chặn truy cập. Nếu Action hoặc Worker lỗi, xem `data/status.json` và thông báo trong app.
- Số cổ phiếu ước tính = vốn góp / 10.000 nên EPS, P/E, vốn hóa có thể lệch với một số công ty.
- Cổ tức ước tính từ dòng tiền chi trả cổ tức, không phải cổ tức công bố.
- Ngân hàng và công ty chứng khoán có phần phân tích/chỉ số riêng. Một số chỉ tiêu như nợ xấu, CAR theo chuẩn NHNN và NIM chuẩn không thể tính từ các trường dữ liệu hiện có.
- Công cụ tham khảo, không phải khuyến nghị đầu tư.

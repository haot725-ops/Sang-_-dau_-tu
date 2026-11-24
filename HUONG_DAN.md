# Sàng – hướng dẫn đưa lên GitHub

App 3 bước (Tầm soát → Định giá → Kỹ thuật) chạy bằng số liệu thật: báo cáo tài chính CafeF + giá lịch sử + giá gần thời gian thực.

## 1. Đưa file lên repo (GitHub Pages)
Tạo repo mới (ví dụ `sang`), upload TOÀN BỘ các file/thư mục trong gói này, giữ nguyên cấu trúc:
`index.html`, `sw.js`, `manifest.json`, `icons/`, `fetch_data.py`, `cafef_fetch.py`, `tickers.json`, `worker.js`, `.github/workflows/cafef.yml`.
Settings → Pages → Deploy from branch → main / root.
Lưu ý: thư mục `.github` bắt đầu bằng dấu chấm, upload bằng kéo thả cả thư mục hoặc tạo file thủ công tại đường dẫn `.github/workflows/cafef.yml`.

## 2. Chạy Action lấy dữ liệu (bắt buộc, làm một lần để có số liệu)
Tab Actions → "Lấy dữ liệu" → Run workflow. Chờ vài phút. Xong sẽ có thư mục `data/` (báo cáo, giá, `status.json`).
Sau đó Action tự chạy lúc 16:40 giờ VN các ngày thứ 2–6.
Mở `data/status.json` hoặc phần "Dữ liệu" trong app để xem nguồn nào chạy được, nguồn nào lỗi.
Muốn thêm mã: sửa `tickers.json` (hoặc Run workflow và điền mã vào ô `tickers`).

## 3. Worker giá trực tiếp (tùy chọn)
Cloudflare → Workers → Create → dán nội dung `worker.js` → Deploy. Nếu repo/Pages của ông khác `https://haot725-ops.github.io` thì sửa dòng `ALLOW` trong file.
Dán địa chỉ Worker vào app: Dữ liệu → Worker trung gian. Có Worker thì: giá nhảy theo phiên (9h–15h), thêm mã mới ngay trong app.

## Giới hạn cần biết
- Báo cáo tài chính dùng API công khai nhưng không chính thức của CafeF. Bộ dữ liệu hiện có đã được đối chiếu với API CafeF cho 57 mã vào ngày 24/11/2026; kết quả khớp về kỳ, mã chỉ tiêu và giá trị. Đây là kiểm tra nhất quán với CafeF, chưa phải đối chiếu độc lập với báo cáo gốc doanh nghiệp công bố. API và nguồn giá có thể thay đổi hoặc chặn truy cập; nếu Action lỗi, xem `data/status.json`.
- Số cổ phiếu ước tính = vốn góp / 10.000 nên EPS, P/E, vốn hóa có thể lệch với một số công ty.
- Cổ tức ước tính từ dòng tiền chi trả cổ tức, không phải cổ tức công bố.
- Ngân hàng và công ty chứng khoán có phần phân tích/chỉ số riêng, không áp dụng điểm của doanh nghiệp thông thường. Một số chỉ tiêu như nợ xấu, CAR theo chuẩn NHNN và NIM chuẩn không thể tính từ các trường dữ liệu hiện có.
- Công cụ tham khảo, không phải khuyến nghị đầu tư.

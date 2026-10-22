# MB macOS — phiên bản 1.0.14

Bản phát hành này dùng chung mã tính năng MB cho macOS và Windows. Bộ cài
Windows và gói macOS được phát hành riêng trong cùng đợt.

## Mở ứng dụng

Đóng cửa sổ MB cũ. Giải nén gói phát hành, mở Terminal tại thư mục ứng dụng
và chạy `./run_macos.sh` (lần đầu script sẽ tạo môi trường Python riêng, cài
thư viện và Chromium). Nếu chạy trực tiếp từ workspace hiện tại:

```bash
cd /Users/tranmanhcuong/Projects/TOMS_SLA/TOMS_SLA_MB
.venv/bin/python app.py
```

Giao diện có nút `OneBSS / OTP / Lịch trực` và lựa chọn kênh cảnh báo. Bản
macOS và Windows được phát hành chung số phiên bản, với gói cài riêng.

## Đọc bảng thay vì tải Excel

Mỗi chu kỳ tìm kiếm hai lần theo đúng phạm vi mới, đọc tất cả trang của
`Danh Sách Kết quả`, đối chiếu tổng số phiếu rồi gộp theo `ma_bh`.
Phiếu có trong cả hai lượt chỉ xử lý một lần, lấy bản ở lượt sau.
Không bấm Xuất Excel trên OneBSS. Báo cáo thống kê Excel cục bộ vẫn được tạo.

Các trường lọc và nội dung cảnh báo đều có ánh xạ trong
`ONEBSS_DATA_MAPPING.md`. `ten_nv` vẫn là nguồn của dòng “Người mở phiếu”,
như chương trình hiện hành. Không dùng “Người báo hỏng” thay cho “Người cập nhật”:
hai trường được giữ riêng (`nguoi_bao_hong` và `nguoi_cn`).
“Số ảo” chưa có ánh xạ và không được sử dụng trong các quy tắc hiện tại.
Nếu thiếu cột, thiếu mã/ngày báo hỏng, trùng phiếu khi phân trang hoặc tổng
không khớp, chương trình báo lỗi và không gửi dữ liệu chưa đầy đủ.

## OneBSS và OTP từ xa

Trong `OneBSS / OTP / Lịch trực`:

1. Nhập User, mật khẩu OneBSS và Chat ID cá nhân được phép gửi OTP.
2. Bật tự động đăng nhập lại, bấm `Lưu thiết lập`.
3. Mật khẩu lưu trong Keychain macOS. Để trống ô mật khẩu để sử dụng bản đã lưu.
   Không ghi mật khẩu hoặc OTP vào settings, nhật ký hoặc gói chẩn đoán.
4. Người nhận OTP phải mở cuộc trò chuyện riêng với bot và bấm Start.
5. Khi OneBSS yêu cầu OTP, trả lời riêng cho bot theo một trong ba dạng:
   `410183`, `/otp 410183`, hoặc `ONEBSS OTP: 410183`.
   Dạng cũ `/otp a12b34cd 410183` vẫn được hỗ trợ. Tin trong group,
   người ngoài danh sách và tin nhắn cũ trước yêu cầu OTP bị bỏ qua.
6. Chờ OTP tối đa 180 giây. Thất bại sẽ thử lại sau 60, 120 rồi tối đa 300 giây.
   Nếu tài khoản/mật khẩu bị từ chối ba lần, app chuyển sang chờ đăng nhập
   trực tiếp để tránh tiếp tục gây khóa tài khoản. Bấm Dừng để sửa thiết lập.
7. Thành công sẽ cấu hình lại và chạy lại cả hai lượt tìm kiếm, bỏ dữ liệu
   dở dang trước khi hết phiên. Có thể đăng nhập trực tiếp khi đang chờ.
   Sau mọi lần xác nhận đăng nhập trên bản MB macOS, app tự bắt đầu cấu hình
   và chạy quy trình. Không cần bấm nút 2.
8. Nếu app đã dừng vì hết thời gian chờ OTP, gửi `/resume` trong chat riêng
   với bot từ một Chat ID đã cấu hình ở mục nhận OTP. App sẽ thử đăng nhập lại;
   khi bot yêu cầu OTP, gửi mã theo một trong các dạng ở trên. Lệnh trong group
   hoặc từ Chat ID chưa được cho phép sẽ bị bỏ qua. Listener hoạt động sau khi
   app đã được mở và cấu hình trên máy; không yêu cầu chạy nền trước lần mở đầu.

Nên dùng bot dành riêng cho MB nếu bật OTP. Không chạy hai ứng dụng đọc
`getUpdates` cùng bot; không dùng bot đang có webhook. App không xóa webhook
của dịch vụ khác. Trong khi quy trình chạy, chức năng Lấy Chat ID bị chặn
để không tranh luồng OTP. Đây là các giới hạn của
[Telegram getUpdates](https://core.telegram.org/bots/api#getupdates).

Ô OTP và nút Xác nhận đã được đối chiếu với giao diện đăng nhập và mã giao
diện công khai hiện tại của OneBSS (`Mã OTP`, `Xác nhận`). Chưa xác thực bằng
tài khoản thật hoặc OTP thật; cần thử một lần trước khi vận hành không giám sát.
Chỉ tự nhập thông tin tại HTTPS trên `onebss.vnpt.vn`.

## Lịch trực và nơi nhận

1. Chọn tháng dạng `2026-10`, bấm `Import lịch Excel` và chọn file lịch tháng 10.
   Tháng phải khớp tiêu đề file, sheet `Bang ca` phải đủ ngày.
2. Bấm `Import ID Telegram`, chọn file danh sách ID trong thư mục MB.
   File cung cấp có 20 nhân sự trực, tất cả đều khớp danh sách Telegram.
3. `Xem tháng` hiển thị như bảng Excel: mỗi hàng là một nhân sự, mỗi cột là
   một ngày, có thứ trong tuần. Cột tên được giữ cố định khi kéo thanh ngang.
   Tên và cột `Telegram Chat ID` đều được giữ cố định; người chưa khớp danh
   bạ hiển thị `Chưa có ID`. `HC`/`Đêm` là ca trực, `–` là nghỉ.
   Bấm ô cần sửa (hoặc chọn tên rồi chọn ngày), chọn HC/Đêm/Nghỉ rồi
   `Lưu đổi ca`. Để đổi ca giữa hai người, sửa cả hai ô tương ứng.
   Các thay đổi lưu cục bộ; không sửa Excel gốc.
4. Trên cửa sổ chính chọn `Group`, `Nhân sự trong ca` hoặc `Cả hai`.
   Cần dừng và chạy lại để áp dụng lựa chọn mới vào quy trình đang chạy.

## Cảnh báo Zalo riêng và trong group

1. Nhập `Zalo Bot token` ở cửa sổ chính. Token được lưu trong Keychain macOS;
   không ghi vào settings hoặc gói chẩn đoán.
2. Mở `OneBSS / OTP / Lịch trực`, chọn tháng, import lịch trực và danh sách
   ID Telegram như trước. File danh sách nhân sự có thể thêm cột `Zalo Chat ID`
   cạnh `ID Telegram`; khi import, ứng dụng đọc cột Zalo nếu có nhưng không sửa
   file Excel gốc.
3. Có thể đăng ký tự động bằng cách nhắn riêng cho bot theo cú pháp
   `Họ Và Tên đăng ký nhận cảnh báo` (hoặc dùng dấu gạch dưới trước cụm
   `Đăng ký nhận cảnh báo`). App cần đang mở, đã có lịch trực được import,
   Zalo Bot token được lưu và bot phải dùng long polling, không cấu hình webhook.
   Tên phải khớp chính xác với nhân sự trong lịch đã import. App trả lời xác nhận
   khi ghép thành công; ID được lưu cục bộ trên máy đang chạy app.
4. Hoặc chọn nhân sự trong bảng, bấm `Gán ID Zalo cho nhân sự chọn`. Người đó
   cần nhắn bot một tin riêng trước; ứng dụng sẽ đọc các cuộc trò chuyện gần đây,
   cho bạn chọn đúng người và lưu Zalo Chat ID cục bộ cạnh Telegram ID.
5. Để gửi vào group, thêm bot vào group, nhắc tên bot hoặc trả lời tin của bot
   trong group rồi bấm `Lấy Group ID Zalo`. Chọn group tìm thấy; ID được lưu
   trong cấu hình máy này. Có thể nhập ID thủ công nếu đã biết.
6. Chọn kênh cảnh báo trên cửa sổ chính:
   - `Telegram`: giữ cách gửi Telegram hiện tại và đích nhận Group/nhân sự/cả hai.
   - `Zalo`: gửi theo nơi nhận đã chọn: group, nhân sự trực hoặc cả hai.
   - `Cả hai`: gửi Telegram và Zalo theo nơi nhận đã chọn.
7. Nếu thiếu Zalo Chat ID cá nhân, ứng dụng cảnh báo và bỏ qua người chưa có ID;
   vẫn cho phép chạy. Group Zalo và các nhân sự đã có ID vẫn nhận cảnh báo.
8. Bấm nút 2 để kiểm tra cấu hình và chạy. Mỗi máy tự lưu Zalo ID trong
   `duty_zalo_contacts.json`; các ID không được tải lên Google Sheet.

Việc lấy ID và nhận lệnh đăng ký dùng `getUpdates` của bot, nên chỉ dùng được
nếu bot chưa cấu hình webhook và không có chương trình khác đang đọc cập nhật
cùng lúc. Tài liệu Zalo mô tả long polling cho chạy thử/phát triển local và
webhook là cơ chế thay thế; do đó không chạy nhiều máy cùng lúc với chung bot
token nếu cần đảm bảo mọi đăng ký được xử lý. Mỗi app chỉ lưu ID trên máy
đang chạy. Chưa xác minh gửi tin với token
thật hoặc tài khoản nhân sự thật; cần dùng `Gán ID Zalo...` và kiểm tra thực tế
trên một nhân sự trước khi bật kênh Zalo cho ca trực.

HC là 08:00–17:00. Đêm bắt đầu 17:00 ngày ghi trên lịch, kết thúc 08:00
hôm sau, theo giờ Việt Nam. Trước 08:00 ngày đầu tháng cần lịch tháng trước.
Tháng đang xem không thay thế tháng thực tế dùng để xác định ca hiện tại.

Mọi phiếu đủ điều kiện được gửi theo kênh và đích nhận đã chọn; cảnh báo riêng
Zalo gửi tới nhân sự đang trực có ID hợp lệ, không lọc theo người mở phiếu.
Người thiếu ID được nêu trong cảnh báo cấu hình và bị bỏ qua; app vẫn chạy.
Group Zalo nhận cảnh báo độc lập với ID Zalo của từng nhân sự.
Mốc 120 phút và các mốc 60 phút tiếp theo được lưu riêng theo người nhận;
không ghi nhận mốc nếu gửi thất bại. Người mới vào ca có thể nhận mốc hiện
tại chưa từng gửi cho họ. Group vẫn giữ lịch sử mốc cảnh báo cũ.

Lịch và danh bạ lưu tại `~/Library/Application Support/TOMS-SLA-MB/`
trong `duty_rosters.json`, `duty_contacts.json` và `duty_zalo_contacts.json`.
Không đưa danh sách nhân sự, mật khẩu hoặc token lên repository public.

## Kiểm tra trước khi dùng chính thức

```bash
cd /Users/tranmanhcuong/Projects/TOMS_SLA/TOMS_SLA_MB
.venv/bin/python -m unittest -v test_macos_preview.py
```

Test offline bao gồm file lịch thật, ranh giới ca qua tháng, ánh xạ bảng,
phân trang/phiếu trùng, mốc SLA, OTP sai người/sai mã/cũ, lỗi gửi không mất
mốc, giao diện Tk và đọc grid bằng Chromium với dữ liệu giả.
Không gửi Telegram thật và không thử tài khoản OneBSS thật.

Cần thử thực tế hai lượt tìm kiếm, số phiếu và một lần hết phiên/OTP.

Phát hiện thêm trong mã SLA cũ: thời gian được làm tròn phút, màu xanh áp
dụng tới đúng 20 phút và màu vàng tới đúng 40 phút. Bản này giữ nguyên
logic cũ; chưa sửa các ranh giới này trong phạm vi tính năng mới.
Test có cảnh báo tương thích pandas/NumPy và cảnh báo đọc Data Validation
của openpyxl; không ghi lại hoặc thay đổi workbook nguồn.

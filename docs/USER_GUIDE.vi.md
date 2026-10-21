# Hướng dẫn fccsv v3 (bản đang phát triển)

## Hiểu ba nơi lưu dữ liệu

- **CSV** là file trong dự án game, gồm dòng tên cột, dòng kiểu dữ liệu,
  rồi các dòng dữ liệu. Thứ tự dòng có thể quan trọng.
- **Sheet** là bảng Google tương ứng. Sheet không có dòng kiểu dữ liệu CSV.
- **BASE** là phiên bản đã được v3 xác minh ở lần đồng bộ trước. V3 lưu BASE,
  lịch sử và bản sao để phục hồi trong `.fccsv3/state.sqlite3`. Bạn không cần
  tạo hay sửa khóa dòng để dùng hằng ngày.

## Quy trình thường dùng

1. Sửa CSV hoặc Sheet như bình thường.
2. Chạy `fccsv status` để thấy bảng nào đổi.
3. Chạy `fccsv diff BossLootData` để xem cột, dòng và ô khác nhau.
4. Chạy `fccsv plan push BossLootData` để xem thao tác có được phép không.
5. Nếu muốn lấy CSV làm nguồn, chạy `fccsv push BossLootData`.
   Nếu muốn lấy Sheet làm nguồn, chạy `fccsv pull BossLootData`.
   Nếu mỗi bên sửa các ô khác nhau sau BASE, chạy `fccsv sync BossLootData`.

`push` và `pull` luôn viết theo một chiều được nêu trong tên lệnh. Viết đúng
thứ tự: `fccsv push BossLootData --force`, **không phải** `--force push`.
`--force` xác nhận chọn chiều đó dù phía đích cũng đã đổi. Nó không bỏ qua lỗi
CSV hoặc yêu cầu xem lại schema. V3 luôn in nguyên lệnh gợi ý có thể sao chép.

## Trước khi chuyển từ v2

Chạy `fccsv doctor` để kiểm tra trạng thái v2, CSV, Sheet, BASE và operation
v3 đang chờ phục hồi. Cảnh báo khóa `ItemId` cũ không còn duy nhất là gợi ý
để xem `diff`; v3 sẽ hiển thị cột nào được dùng để nhận diện dòng. Dừng các
tiến trình v2 đang ghi trước khi chuyển sang v3.

Chạy `fccsv backup --output cutover.zip` để lưu bản sao có checksum. Đường
dẫn ZIP phải là tên file mới. Backup gồm byte CSV, giá trị Sheet, config và
state hiện có; kiểm tra lại hai phía trước khi hoàn tất ZIP. Backup chỉ chứa
giá trị hiển thị từ Sheets, không chứa công thức hay định dạng. Hiện chưa thử nghiệm ghi Google
thật, nên đây vẫn là công cụ chuẩn bị cutover, chưa phải tín hiệu cho phép
chuyển Sheet sản xuất.

ZIP có `manifest.json` (checksum và đường dẫn CSV gốc), `csv/NNN.csv`,
`sheet/values.json`, `config/.fccsv.json` và, nếu tồn tại,
`config/.fccsv-state.json`, `legacy-base/NNN.json`, `v3/state.sqlite3`.
Để có thể quay về nguyên trạng cả công thức và định dạng, hãy tạo thêm bản sao
Google spreadsheet bằng công cụ của Google trước cutover. ZIP của v3 không
thay thế bản sao đó; Sheets không cung cấp ảnh chụp đồng thời tất cả tab.
Lệnh ghi của v3 xóa toàn bộ giá trị trong tab rồi ghi lại bảng. Trước khi
xóa, v3 kiểm tra và từ chối tab có công thức vì không thể bảo toàn công thức
qua thao tác này. Hãy dùng tab dữ liệu thuần; kiểm tra công thức cũng không
thể ngăn thay đổi đồng thời giữa lúc đọc và ghi Sheet.

Khi cần xử lý vài bảng, viết các tên sau lệnh, chẳng hạn
`fccsv push ActiveSkillData ProgressionAccessData --force`, hoặc chọn file
bằng `fccsv push Assets/CSV/BossLootData.csv --force`. Từ gốc dự án, dùng
`fccsv plan push . --force` rồi `fccsv push . --force` để chọn các CSV đã
khai báo nằm dưới thư mục hiện tại, giống Git. Muốn chọn mọi bảng từ bất kỳ
thư mục nào, dùng `fccsv plan push --all --force` rồi `fccsv push --all --force`.
Có thể ghép nhiều đường dẫn và tên bảng trong một lệnh. V3 kiểm tra mọi bảng
đã chọn trước khi ghi và dừng nếu một bảng bị chặn.
Chế độ text in một dòng cho mỗi bảng ngay khi hoàn tất; `--verbose` mở rộng
thông tin. Dòng cuối cho biết số bảng đã xong và thời gian chạy. Các bảng
không cần ghi được xác minh bằng lượt đọc Sheet theo nhóm rồi cập nhật BASE.
Bảng cần ghi vẫn được đọc lại ngay trước và sau thao tác để tránh ghi đè thay
đổi mới; vì vậy số yêu cầu Google tăng theo số bảng thực sự cần ghi. V3
lưu tạm danh sách tab một phút để giảm API call. Khi Google trả HTTP 429,
CLI báo rõ và tự đợi 60 giây để thử lại tối đa ba lần. Nếu vẫn dừng, chạy
`fccsv recover` để kiểm tra operation đang chờ trước khi chạy lại. Nhiều
tab Google không thể ghi như một giao dịch duy nhất: nếu bảng sau gặp lỗi,
các bảng trước đã hoàn tất vẫn giữ nguyên. Kết quả lệnh nêu rõ các bảng đã
xong và operation cần phục hồi. Bảng có cột Sheet mới cần kéo riêng với `--type`; tùy chọn này chỉ dùng
cho đúng một bảng trong lệnh `pull`.

Khi cột Sheet mới được kéo về CSV, khai báo kiểu cho cột đó:

```powershell
fccsv pull Items --force --accept-schema --type Extra=text
```

Nếu lệnh ghi bị gián đoạn, chạy `fccsv recover` để lấy mã operation, rồi
`fccsv recover <mã>` để xem hai phía đang ở bản trước, bản sau, hay bản khác.
Chọn `--continue` để hoàn tất kết quả dự kiến hoặc `--restore` để trả lại bản
trước. Nếu dữ liệu hiện tại không trùng cả hai bản đã lưu, v3 yêu cầu bạn xem
lại trước khi cho phép `--force`.

Nếu muốn đảo ngược một operation đã hoàn tất, xem trước bằng
`fccsv undo BossLootData --dry-run`, rồi chạy `fccsv undo BossLootData`.
Chỉ operation hoàn tất mới nhất của bảng đó có thể Undo; nếu CSV hoặc Sheet
đã thay đổi sau đó, v3 sẽ chặn để tránh ghi đè. Undo cũng có lịch sử và có
thể phục hồi nếu bị gián đoạn.

Nếu phải quay về v2 sau cutover, dừng cả hai công cụ ghi trước. Xử lý mọi
operation v3 đang chờ bằng `recover`; nếu chỉ cần đảo thao tác mới nhất, dùng
`undo` theo hướng dẫn trên. Với rollback toàn bộ, dùng `manifest.json` để
khôi phục đúng các CSV và state/BASE v2 từ **cùng một ZIP trước cutover**,
đồng thời khôi phục các tab Sheet và `_fccsv` từ bản sao spreadsheet cùng thời
điểm. `sheet/values.json` chỉ là phương án khôi phục giá trị; nó không khôi
phục công thức hay định dạng. Sau đó so sánh lại cả hai phía với bản đã lưu
trước khi cho v2 ghi. V3 chưa có lệnh tự động khôi phục toàn bộ ZIP.

Trước khi đọc Sheet, dùng `fccsv auth status` để kiểm tra vị trí token.
Mỗi người chạy `fccsv auth login` bằng tài khoản Google của mình. Có thể
copy `tools/fccsv-v3/.env.example` thành `.env.local` ở gốc dự án rồi sửa
`FCCSV3_TOKEN_FILE` và `FCCSV3_CREDENTIALS_FILE` thành đường dẫn file trên máy
đó. `.env.local` chỉ chứa **đường dẫn**, không chứa nội dung token; Git bỏ qua
file này. Đường dẫn truyền bằng `--token` hoặc `--credentials` được ưu tiên
trước biến môi trường, rồi `.env.local`, rồi `.fccsv.json`. Không chia sẻ
`token.json` cho người khác. Token có sẵn của v2 vẫn dùng được.

**Hiện tại chỉ thử với CSV tạm và Sheet giả.** Chưa chạy các lệnh ghi v3 trên
Sheet sản xuất. Dùng `fccsv tui` để xem trạng thái, diff, preview và xác
nhận thao tác cho từng bảng. TUI hiện lệnh CLI tương ứng để sao chép. Các ca
đổi schema phức tạp vẫn dùng CLI. Quy trình đăng nhập trình duyệt chưa được
kiểm tra trực tiếp trong môi trường này.

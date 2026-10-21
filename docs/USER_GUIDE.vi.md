# Hướng Dẫn Sử Dụng gitcsv (User Guide)

`gitcsv` là công cụ đồng bộ và quản lý phiên bản hai chiều giữa các file CSV trong dự án game và Google Sheets, hoạt động dựa trên mô hình tư duy quen thuộc của Git.

---

## 1. Cài đặt

Mở PowerShell hoặc Command Prompt và chạy lệnh:

```bash
# Cài đặt từ thư mục mã nguồn gitcsv
pip install -e tools/gitcsv
```

Sau khi cài đặt, lệnh `gitcsv` sẽ có sẵn ở bất kỳ thư mục nào trên máy tính của bạn.

---

## 2. Khởi tạo dự án (`gitcsv init`)

Di chuyển đến thư mục dự án game (nơi chứa thư mục `Assets/CSV`):

```bash
cd "D:/Craftland Projects/PlantsVsZombiesFCC"
```

Chạy lệnh khởi tạo:

```bash
gitcsv init "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit" --csv-dir Assets/CSV
```

*Tool sẽ tự động:*
* Trích xuất Spreadsheet ID từ link Google Sheet.
* Quét toàn bộ file `.csv` trong `Assets/CSV` và đối chiếu với các sheet tab trên Google Sheets để tự động ánh xạ 1-1.
* Tạo file cấu hình `.gitcsv/config.json`.
* Tự động thêm các file nhạy cảm (`.gitcsv/`, `.env.local`, `*.token.json`) vào `.gitignore`.

---

## 3. Quy trình làm việc hàng ngày

### A. Kiểm tra trạng thái đồng bộ (`status`)

Xem bảng trạng thái chi tiết có màu sắc:
```bash
gitcsv status
```

Xem trạng thái ngắn gọn 1 dòng/bảng (tương tự `git status -s`):
```bash
gitcsv status -s
```

*Ý nghĩa các ký hiệu:*
* ` = ` : Hai bên hoàn toàn khớp nhau (In sync).
* ` M ` : File CSV cục bộ có sửa đổi.
* ` R ` : Sheet trên Google có người khác sửa đổi.
* `?? ` : Bảng mới chỉ có ở CSV (chưa có trên Sheets).
* ` D ` : Bảng chỉ có trên Sheets (chưa có ở CSV).
* `UU ` : Cả hai bên cùng sửa đổi (có thể xung đột).

---

### B. So sánh thay đổi chi tiết (`diff`)

Xem chi tiết từng cell, từng dòng và schema bị lệch:
```bash
# Xem diff toàn bộ các bảng có thay đổi
gitcsv diff

# Xem diff riêng 1 bảng cụ thể
gitcsv diff BossLootData
```

*Quy ước màu:*
* Màu xanh lá (`+`): Dòng hoặc cột chỉ có ở CSV.
* Màu đỏ (`-`): Dòng hoặc cột chỉ có ở Google Sheets.
* Màu vàng (`~`): Giá trị cell bị thay đổi giữa CSV và Sheet.

---

### C. Đẩy dữ liệu từ CSV lên Google Sheets (`push`)

Đẩy thay đổi của 1 bảng:
```bash
gitcsv push BossLootData
```

Đẩy thay đổi của toàn bộ các bảng:
```bash
gitcsv push --all
# hoặc viết tắt:
gitcsv push -a
```

*Các tùy chọn an toàn:*
* `-n` / `--dry-run`: Chạy thử xem trước kết quả mà không ghi thật (`gitcsv push -na`).
* `-f` / `--force`: Ép buộc ghi đè lên Google Sheets ngay cả khi người khác vừa sửa trên Sheet.
* `--accept-schema`: Cho phép đẩy khi bạn có thêm/bớt/đổi thứ tự cột.
* **Gộp cờ ngắn gọn**: Bạn có thể gõ `gitcsv push -fna` (chạy thử, ép buộc, cho tất cả bảng).

---

### D. Kéo dữ liệu từ Google Sheets về CSV (`pull`)

Kéo dữ liệu mới nhất từ Google Sheets về ghi đè file CSV:
```bash
# Kéo 1 bảng
gitcsv pull BossLootData

# Kéo tất cả các bảng
gitcsv pull --all
```

Tương tự `push`, bạn có thể dùng `-n` (chạy thử) hoặc `-f` (ghi đè thay đổi chưa lưu ở local).

---

### E. Hợp nhất 3 chiều thông minh (`sync`)

Khi cả bạn và đồng đội đều sửa bảng (bạn sửa cột `Cost`, đồng đội sửa cột `Stock` trên Sheet):
```bash
gitcsv sync BossLootData
```
`gitcsv` sẽ tự động ghép các ô dữ liệu độc lập mà không làm mất công sức của ai!

#### Giải quyết xung đột (Conflict Resolution):
Nếu cả hai người cùng sửa **đúng một ô dữ liệu** thành các giá trị khác nhau:
1. **Interactive Prompt**: CLI sẽ hỏi trực tiếp:
   ```text
   ? Choose conflict resolution strategy [ours/theirs/abort] (abort): 
   ```
   * Chọn `ours`: Giữ giá trị của bạn ở CSV.
   * Chọn `theirs`: Lấy giá trị của đồng đội trên Sheet.
2. **Flag trực tiếp**:
   ```bash
   gitcsv sync BossLootData --ours
   # hoặc:
   gitcsv sync BossLootData --theirs
   ```

---

### F. Khôi phục file khi lỡ tay sửa hỏng (`restore`)

Nếu bạn đang sửa file CSV cục bộ nhưng lỡ tay làm hỏng dữ liệu và muốn quay lại bản đồng bộ gần nhất:
```bash
gitcsv restore BossLootData
```
File `Assets/CSV/BossLootData.csv` sẽ được hoàn trả về nguyên trạng bản BASE gần nhất trong tích tắc.

---

### G. Hoàn tác lệnh vừa ghi (`undo`)

Nếu bạn vừa lỡ tay chạy `push` hoặc `pull` nhầm bảng và muốn hoàn tác lại trạng thái trước khi ghi:
```bash
gitcsv undo BossLootData
```
Cả file CSV cục bộ và tab trên Google Sheets sẽ được phục hồi lại đúng ảnh chụp (before-image) trước lúc ghi!

---

### H. Khám sức khỏe dự án (`doctor`)

Kiểm tra toàn diện tính toàn vẹn của dự án:
```bash
gitcsv doctor
```
Lệnh này sẽ kiểm tra:
* File cấu hình `.gitcsv/config.json`.
* Kiểm tra CSV có bị thiếu cột, sai kiểu dữ liệu hay lỗi cú pháp không.
* Kiểm tra trên Google Sheets có ai vô tình gõ công thức (`=SUM(...)`) vào tab dữ liệu không.
* Kiểm tra có giao dịch nào bị đứt đoạn cần phục hồi không.

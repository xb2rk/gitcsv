# Hướng Dẫn Tích Hợp & Chia Sẻ OAuth Cho Cả Team

Tài liệu này giải thích cách chia sẻ công cụ `gitcsv` cho toàn bộ thành viên trong dự án (Game Designer, Content Creator, Programmer) mà **không bắt mọi người phải tự tạo dự án trên Google Cloud Console**.

---

## 1. Bản chất của Google OAuth 2.0 cho Ứng dụng Desktop

Khi làm việc với Google Sheets API, nhiều người thường lo sợ:
> *"Nếu tôi đưa file `credentials.json` cho cả team hoặc commit vào repo, có bị lộ tài khoản cá nhân hay bảo mật không?"*

### Sự khác biệt giữa `credentials.json` và `token.json`:
1. **`credentials.json` (OAuth Client ID & Client Secret)**:
   * Đây chỉ là **"Chứng minh nhân dân của phần mềm"**, định danh cho ứng dụng `gitcsv` trên Google Cloud.
   * Theo chuẩn bảo mật quốc tế **RFC 8252** và tài liệu chính thức của Google: Các ứng dụng Desktop (Installed Application) được xếp vào loại **Public Client**. Client Secret của Desktop App **KHÔNG PHẢI LÀ MẬT KHẨU CÁ NHÂN**.
   * Nó không cấp bất kỳ quyền truy cập dữ liệu nào nếu không có user đăng nhập.
2. **`token.json` (Access Token & Refresh Token)**:
   * Đây mới là **"Chìa khóa dữ liệu của từng cá nhân"**, sinh ra sau khi một người mở trình duyệt và đồng ý đăng nhập tài khoản Google của họ.
   * File này **TUYỆT ĐỐI KHÔNG COMMITT LÊN GIT** (đã được `gitcsv` đưa vào `.gitignore`).

---

## 2. Ba Cách Chia Sẻ Cho Cả Team

### Cách 1: Tích Hợp Sẵn OAuth Desktop Client (Khuyên Dùng Cho Cá Nhân)
Trưởng nhóm (Lead) chỉ cần tạo OAuth Client một lần duy nhất trên Google Cloud Console:
1. Vào Google Cloud Console $\rightarrow$ Tạo Project $\rightarrow$ Bật Google Sheets API & Google Drive API.
2. Cấu hình **OAuth consent screen** (chọn Internal nếu dùng Google Workspace công ty, hoặc External và thêm email thành viên vào mục Test Users).
3. Tạo Credentials $\rightarrow$ **OAuth client ID** $\rightarrow$ Chọn Application type là **Desktop app**.
4. Tải file JSON về và đặt tên là `credentials.json` ở project root (hoặc paste trực tiếp chuỗi JSON).

**Trải nghiệm của thành viên trong team**:
* Người khác chỉ cần clone repo về, gõ:
  ```bash
  gitcsv auth login
  ```
* Trình duyệt tự bật lên $\rightarrow$ Đăng nhập tài khoản Google cá nhân của họ $\rightarrow$ Cấp quyền thành công!
* Token sẽ lưu riêng trên máy người đó (`.gitcsv/token.json` hoặc `~/.gitcsv/token.json`).

---

### Cách 2: Sử Dụng Service Account (Zero-Login / Tự Động Hoàn Toàn)
Nếu team bạn có những designer ngại bấm đăng nhập trình duyệt, hoặc muốn chạy tự động trên CI/CD:
1. Trên Google Cloud Console $\rightarrow$ Tạo **Service Account** (ví dụ `pvz-sync-bot@my-project.iam.gserviceaccount.com`).
2. Tạo và tải về **Key file** dạng JSON (đặt tên `service_account.json`).
3. Mở Google Spreadsheet của dự án $\rightarrow$ Bấm nút **Share (Chia sẻ)** $\rightarrow$ Dán email của Service Account vào và cấp quyền **Editor (Người chỉnh sửa)**.
4. Trong file `.gitcsv/config.json` cấu hình:
   ```json
   {
     "auth": {
       "mode": "service_account",
       "credentials_file": "service_account.json"
     }
   }
   ```

**Trải nghiệm của thành viên trong team**:
* Không cần đăng nhập gì cả!
* Mọi thành viên chỉ cần gõ `gitcsv push` hay `gitcsv pull` là hệ thống tự động giao tiếp với Google Sheets qua Service Account ngay lập tức.

---

### Cách 3: Xác Thực Toàn Cục (Global User Auth)
Nếu một thành viên trong team làm việc trên nhiều repository/project khác nhau:
* Thành viên đó chỉ cần đăng nhập 1 lần duy nhất với cờ `--global`:
  ```bash
  gitcsv auth login --global
  ```
* File token sẽ lưu tại thư mục cá nhân `~/.gitcsv/token.json`.
* Mọi dự án khác trên máy tính khi chạy `gitcsv` sẽ tự động dùng chung token này mà không cần đăng nhập lại.

---

## 3. Thứ tự Tìm Kiếm Credentials Của `gitcsv`

Để thuận tiện tối đa cho người dùng, `gitcsv` sẽ tự động tìm `credentials.json` theo thứ tự:
1. Tham số dòng lệnh: `--credentials <path>`
2. Biến môi trường: `GITCSV_CREDENTIALS_FILE` (hoặc trong `.env.local`)
3. Thư mục dự án: `.gitcsv/credentials.json`
4. Thư mục gốc dự án: `./credentials.json`
5. Thư mục toàn cục người dùng: `~/.gitcsv/credentials.json`

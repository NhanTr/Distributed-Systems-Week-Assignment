# Day 9 - Tóm tắt và lời giải

Nguồn đề: [lect09_security_concepts.pdf](lect09_security_concepts.pdf). Số trang dưới đây là số trang của file PDF: bài tính ở **43-44**, lab ở **45-46**.

## 1. Kiến thức cần nhớ

| Nội dung | Hiểu ngắn gọn |
| --- | --- |
| CIA | **Bí mật**: đúng người mới xem được. **Toàn vẹn**: phát hiện sửa trái phép. **Sẵn sàng**: dịch vụ dùng được khi cần. |
| 4 loại đe dọa | Interception: nghe lén; interruption: làm gián đoạn; modification: sửa dữ liệu; fabrication: tạo thông điệp giả. |
| Policy / mechanism | Policy là **quy định** ai được làm gì; mechanism là **cách thực thi**, như mã hóa hay kiểm tra quyền. |
| TCB, RISSC | Thu nhỏ phần hệ thống phải tin cậy; cô lập thành phần nhạy cảm và chỉ cho truy cập qua giao diện nhỏ, dễ kiểm tra. |
| Mã hóa đối xứng | Hai bên dùng khóa bí mật chung. AES nhanh, phù hợp dữ liệu RPC. |
| Mã hóa bất đối xứng | Có khóa công khai và khóa riêng. RSA có thể dùng bảo vệ khóa phiên; dữ liệu lớn dùng AES. |
| Hash / HMAC / chữ ký số | Hash tạo dấu vân tay; HMAC thêm khóa chung để kiểm tra toàn vẹn và nguồn gửi; chữ ký dùng khóa riêng và kiểm tra bằng khóa công khai. |
| Diffie-Hellman, PKI | DH tạo bí mật chung nhưng cần xác thực để tránh kẻ đứng giữa. Chứng chỉ CA giúp gắn danh tính với khóa công khai. |
| Nonce | Số ngẫu nhiên dùng một lần. Lưu nonce đã nhận để phát hiện phát lại. HMAC đúng vẫn có thể là gói cũ! |
| KDC, Kerberos | KDC cấp khóa phiên và vé. Kerberos dùng AS/TGS để hỗ trợ đăng nhập một lần, rồi lấy vé truy cập dịch vụ. |
| ACL / capability | ACL: mỗi tài nguyên ghi ai có quyền. Capability: chủ thể giữ vé quyền truy cập khó giả mạo. |
| Firewall / sandbox | Firewall lọc lưu lượng; sandbox hạn chế quyền của mã không tin cậy. |

**Phân biệt:** mã hóa bảo vệ bí mật; HMAC phát hiện giả mạo; nonce chống phát lại. Cần phối hợp cả ba. HMAC không tạo tính chống chối bỏ vì hai bên đều biết khóa chung. TLS thông thường xác thực server; xác thực client bằng chứng chỉ cần cấu hình riêng.

## 2. Giải bài tính cuối bài

### RSA - trang 43

Cho `p = 3`, `q = 11`, `e = 3`, bản rõ `P = 4`:

1. `n = p × q = 33`.
2. `φ(n) = (p - 1)(q - 1) = 20`.
3. `gcd(3, 20) = 1` nên chọn `e = 3` hợp lệ.
4. Tìm `d` sao cho `3d mod 20 = 1`: chọn `d = 7` vì `3 × 7 = 21`.
5. Khóa công khai `(e, n) = (3, 33)`; khóa riêng `(d, n) = (7, 33)`.
6. Mã hóa: `C = 4³ mod 33 = 64 mod 33 = 31`.
7. Giải mã: `31² mod 33 = 4`, `31⁴ mod 33 = 16`, nên `31⁷ mod 33 = 16 × 4 × 31 mod 33 = 4`.

Đây là số nhỏ để học phép tính; không dùng ví dụ RSA này để bảo vệ dữ liệu thật.

### Số lượng khóa - trang 43

Với `N = 100` máy:

- Khóa chung cho từng cặp: `100 × 99 / 2 = 4.950` khóa.
- KDC: `100` khóa dài hạn, mỗi máy dùng một khóa chung với KDC.
- Giảm `4.850` khóa, tương đương `4.850 / 4.950 × 100 ≈ 97,98%` (xấp xỉ 98%). Chưa tính các khóa phiên được cấp khi giao tiếp.

### Chữ ký ngưỡng - trang 44

Có `N = 7` server, tối đa `c = 2` server bị chiếm quyền. Để hai server xấu không tự ký được, cần `m > c`; nhỏ nhất **`m = 3`**. Khi hai server xấu không hợp tác, vẫn còn 5 server tốt, đủ lấy 3 phần chữ ký.

Điều này chỉ xác định ngưỡng chống giả mạo chữ ký. `m = 3` tự nó không bảo đảm dữ liệu đúng hoặc đồng thuận Byzantine; các server tốt còn phải kiểm tra nội dung trước khi ký.

### Diffie-Hellman - trang 44

Cho `p = 23`, `g = 5`, Alice giữ `a = 6`, Bob giữ `b = 15`:

- Alice gửi `A = 5⁶ mod 23 = 8`.
- Bob gửi `B = 5¹⁵ mod 23 = 19`.
- Alice tính `K = 19⁶ mod 23 = 2`.
- Bob tính `K = 8¹⁵ mod 23 = 2`.

Hai bên nhận cùng bí mật **`K = 2`**, không gửi trực tiếp `a` và `b`. Đây cũng là bộ số minh họa nhỏ.

## 3. Giải pháp lab - trang 45-46

### Ý tưởng

Alice muốn gọi `Write('Balance', 500)` trên Bob. Alice và Bob chưa có khóa chung, nên nhờ KDC cấp một **khóa phiên `K_AB`**. Vé giống phong bì chỉ Bob mở được: Alice chuyển tiếp nhưng không sửa được nội dung.

Có 3 tiến trình ứng dụng thực sự: **KDC**, **Client Alice**, **Resource Server Bob**. Client nằm trong tiến trình `run_demo.py`; hai daemon được khởi tạo bằng multiprocessing và mọi thông điệp giao thức đi qua TCP.

### Luồng Needham-Schroeder

Ký hiệu `E_K(x)` trong mô phỏng nghĩa là AES-CBC + HMAC của nội dung `x` bằng các khóa con suy ra từ `K`.

| Bước | Thông điệp | Mục đích |
| --- | --- | --- |
| 1 | Alice → KDC: `Alice, Bob, R_A` | Xin khóa để nói chuyện với Bob; `R_A` ngẫu nhiên. |
| 2 | KDC → Alice: `E_KA(R_A, Bob, K_AB, ticket, session_id, thời hạn)` | Alice giải mã và kiểm tra nonce, danh tính, thời hạn. `ticket = E_KB(Alice, Bob, K_AB, session_id, thời hạn)`. |
| 3 | Alice → Bob: `ticket, E_KAB(nonce_mới, session_id)` | Bob mở vé bằng `K_B`, kiểm tra đích đến, thời hạn và vé chưa dùng. |
| 4 | Bob → Alice: `E_KAB(R_B, nonce_mới, session_id)` | Bob gửi thử thách; Alice kiểm tra phản hồi thuộc phiên hiện tại. |
| 5 | Alice → Bob: `E_KAB(R_B - 1, session_id)` | Chứng minh Alice có khóa phiên. Bob xác nhận rồi mới nhận RPC. |

Khóa dài hạn: `K_A` chỉ cấp cho Alice/KDC; `K_B` chỉ cấp cho Bob/KDC. Khóa phiên do KDC tạo mới. Mã khởi động demo tạo hai khóa dài hạn trong tiến trình client để thiết lập lab; đây là giả định cấp phát tin cậy, không mô phỏng hệ thống cấp phát khóa ngoài thực tế.

Vé được thêm thời hạn 120 giây và mỗi vé chỉ được mở một kết nối. Đây là bổ sung cho mô hình Needham-Schroeder nhằm hạn chế tái sử dụng vé; không triển khai đầy đủ Kerberos.

### Bảo vệ từng RPC

1. Client tạo nội dung `{nonce, op, key, value}`.
2. Suy ra khóa AES và HMAC riêng bằng HKDF từ khóa phiên. Nhãn khác nhau phân biệt client→server, server→client và các bước xác thực.
3. Thêm padding PKCS7; mã hóa AES-256-CBC với **IV ngẫu nhiên 16 byte mới cho mỗi gói**.
4. Tính HMAC-SHA256 trên **cả IV và ciphertext**. Gửi `{iv, ciphertext, mac}` dạng hex trong JSON.
5. Server kiểm tra HMAC **trước khi giải mã**, rồi kiểm tra thời hạn phiên và nonce chưa dùng. Chỉ sau đó mới đọc/ghi dữ liệu.
6. Server lưu nonce đã xử lý. Phản hồi cũng được mã hóa, gắn HMAC và nonce của yêu cầu để client đối chiếu. Gói có MAC sai trả lỗi được bảo vệ với nonce `null`, vì server chưa thể tin nội dung yêu cầu.

**Vì sao không chỉ dùng CBC?** CBC tự nó không xác thực dữ liệu. Cách ghép Encrypt-then-MAC và yêu cầu IV mới được mô tả trong [tài liệu chính thức cryptography](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/); xem thêm [HKDF](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#hkdf).

### Demo chứng minh điều gì?

| Tình huống | Kết quả phải có |
| --- | --- |
| Gửi RPC trước xác thực | `AUTH_FAILED`, không thực thi |
| Xác thực KDC, chuyển vé, trả lời thử thách đúng | Kênh được thiết lập |
| Ghi `Balance = 500` | Thành công |
| Gửi lại nguyên gói ghi cũ | `REPLAY_DETECTED` |
| Lật một bit ciphertext của gói ghi `999` | `AUTH_FAILED` |
| Đọc lại `Balance` | Vẫn là `500` |

### Đối chiếu thang điểm

| Tiêu chí PDF | Điểm | Phần thực hiện |
| --- | --- | --- |
| KDC và cấp vé | 35% | `KDC`, `ResourceServer.authenticate`, `PendingAuthentication`, `Client` |
| AES + HMAC | 30% | `seal`/`unseal`, áp dụng cả yêu cầu và phản hồi |
| Chống replay | 25% | Nonce từng RPC, `Session.seen`, vé dùng một lần, thời hạn |
| Tài liệu và cấu trúc | 10% | README, bài giải, demo, kiểm thử và trace |

### Giới hạn mô phỏng

- Hai tài khoản cố định, xử lý tuần tự từng kết nối; kho dữ liệu và bộ nhớ nonce không lưu xuống đĩa.
- Khởi động lại demo tạo khóa mới, xóa dữ liệu và các phiên cũ. Không hỗ trợ khôi phục phiên sau crash.
- Vé dùng một lần; muốn kết nối lại cần xin vé mới. Đồng hồ giả định đồng bộ vì chạy cùng máy.
- KDC là thành phần tin cậy; nếu bị chiếm quyền sẽ lộ khóa phiên. Thời hạn không loại bỏ hoàn toàn rủi ro khóa phiên bị lộ.
- Minh họa xác thực và bảo mật kênh; chưa có phân quyền nhiều người dùng, chống DoS, thu hồi khóa hay bảo mật chuyển tiếp.

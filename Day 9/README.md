# Day 9: Secure RPC & KDC

Bài lab trang **45-46** PDF (slide 39-40). Python 3.9+, dùng thư viện `cryptography` để mã hóa AES thật.

## Chạy nhanh

Mở terminal tại thư mục assignment:

```bash
cd "Day 9"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_demo.py
python -m unittest -v
```

Demo tự chạy **3 tiến trình ứng dụng**: client chính, KDC và server; giao tiếp TCP localhost qua port tự cấp. Cần cho phép mở socket localhost, không cần Internet sau khi cài thư viện. Demo tự dừng hai daemon khi kết thúc.

Kết quả chính:

```text
Secure Channel Established with Server_Bob!
Write('Balance', 500) | AES-CBC + HMAC | OK.
Exact same packet replayed: REPLAY_DETECTED (expected).
Ciphertext modified: AUTH_FAILED (expected).
Read('Balance') = 500 | Bad packets did not change data.
ALL CHECKS PASSED
```

Lưu lại log chạy thật: `python run_demo.py > execution_trace.log 2>&1`. Thứ tự log từ các tiến trình và PID có thể khác giữa các lần chạy.

## Đọc file nào?

| File | Nội dung |
| --- | --- |
| [SUMMARY_AND_LAB_SOLUTION.md](SUMMARY_AND_LAB_SOLUTION.md) | Tóm tắt tiếng Việt, giải bài tính, giải thích lab |
| [secure_rpc.py](secure_rpc.py) | AES/HMAC, KDC, xác thực, chống replay, socket và client |
| [run_demo.py](run_demo.py) | Chạy lab tự động và kiểm tra kết quả |
| [test_secure_rpc.py](test_secure_rpc.py) | Kiểm thử mã hóa, xác thực, replay và TCP thực tế |
| [execution_trace.log](execution_trace.log) | Log của lần demo đã chạy |

Đây là mô phỏng học tập: dữ liệu và lịch sử nonce ở RAM, vé có hạn 120 giây, chỉ có Alice/Bob và hai thao tác `read`/`write`. Xem giả định và giới hạn trong bài giải.

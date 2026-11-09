# Day 8: Gossip & Quorum Key-Value Store

Bài giải lab trang PDF 41–43 (slide 36–38). Python 3.9+, chỉ dùng thư viện chuẩn. Có 5 OS process S1..S5 giao tiếp bằng TCP localhost, quorum cấu hình được, Push-Pull gossip mỗi 2 giây và tombstone.

## Chạy nhanh

Mở terminal tại thư mục assignment:

```bash
cd "Day 8"
python3 -m unittest -v
python3 run_demo.py --output execution_trace.log
```

Demo tự tạo 5 server, thực hiện các bước, kiểm tra kết quả và tự dừng toàn bộ process. File trace được ghi đè mỗi lần chạy. `replica_logs/S1.log` đến `S5.log` chứa log giao thức từng server. Port được hệ điều hành tự cấp. Cần môi trường cho phép mở TCP listener trên localhost; không cần Internet.

Các bước demo: ghi Bob tới quorum 3; quan sát S4/S5 stale; đọc S3/S4/S5 chọn phiên bản cao nhất; Push-Pull lan dữ liệu; xóa bằng tombstone version 2; cho node còn giữ Bob gặp tombstone; bật gossip ngẫu nhiên để cả 5 node hội tụ; dừng 2 node vẫn đọc/ghi được; dừng thêm 1 node thì báo thiếu quorum.

## Tự thao tác và đổi quorum

```bash
python3 lab.py --nr 3 --nw 3
```

Nhập từng dòng:

```text
write user1 Bob
read user1
state
delete user1
read user1
sync S1 S5
state
stop S1
stop S2
write user2 Alice
read user2
quit
```

Log của terminal tương tác nằm trong `interactive_logs/`, tách riêng log demo.

`state` có thể hiển thị stale data trong khoảng trước khi gossip. `read` luôn dùng quorum. Ghi giá trị có khoảng trắng dùng dấu nháy: `write greeting "Xin chào"`. `stop` mô phỏng crash thật và mất bộ nhớ node đó; kết thúc session bằng quit hoặc Ctrl-D/Ctrl-C.

Ví dụ cấu hình khác: `python3 lab.py --nr 2 --nw 4` hoặc `python3 lab.py --nr 1 --nw 5`. Cặp 2/3 bị từ chối vì không thỏa giao nhau. Thay cấu hình bằng cách khởi động một session mới; không mở nhiều proxy ghi độc lập lên cùng cụm.

## Các file

| File | Nội dung |
| --- | --- |
| SUMMARY_AND_LAB_SOLUTION.md | Tóm tắt tiếng Việt, giải ôn tập, giải pháp lab |
| DESIGN_REPORT.md | Kiến trúc, thuật toán, giả định, giới hạn và rubric |
| gossip_quorum.py | Store, JSON RPC, TCP server, gossip và proxy |
| cluster.py | Tạo và quản lý 5 tiến trình cùng socket |
| lab.py | Terminal tương tác và cấu hình NR/NW |
| run_demo.py | Demo tự động có kiểm tra kết quả |
| test_gossip_quorum.py | Kiểm thử unit và TCP integration |
| execution_trace.log | Log thực thi demo |
| replica_logs/ | Log giao thức từ 5 server |

Một proxy serialize ghi và cấp scalar version. Trạng thái chỉ ở RAM; tombstone được giữ đến hết phiên. Ghi thiếu ACK sau khi đã gửi có thể có tác dụng một phần; phải coi kết quả là chưa xác định. Đọc báo None cho không tồn tại; chuỗi rỗng vẫn là dữ liệu hợp lệ. Chi tiết trong DESIGN_REPORT.md.

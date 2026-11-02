# Báo cáo thiết kế và đối chiếu yêu cầu lab

## Phạm vi và lựa chọn

Đề gốc trang PDF 41–43 cho phép Python/Java/Go, TCP/UDP. Chọn Python 3.9+ và TCP với thư viện chuẩn để không cần cài dependency. Mô phỏng thuần object sẽ không đáp ứng yêu cầu 5 server process. UDP cần thêm xử lý truyền lại và mất gói, không cần thiết cho mục tiêu lab này.

Có 5 process replica độc lập S1..S5, một proxy ở process chính. Parent giữ vòng đời cluster. Mỗi replica có một store riêng, TCP server đa luồng xử lý request và thread gossip. Dùng địa chỉ 127.0.0.1, port hệ điều hành tự chọn; listener được bind trước rồi chuyển cho process con để tránh tranh cổng lúc khởi động. Parent đóng bản sao listener của mình. RPC ping xác nhận server sẵn sàng.

## Trạng thái và giao thức

Bản ghi chứa `value` (chuỗi hoặc null cho tombstone), `version_number` (số nguyên dương), `is_tombstone` (boolean). Khóa chưa có tương đương phiên bản 0 nhưng không lưu một record giả. Chuỗi rỗng là giá trị hợp lệ, khác với không tồn tại.

Request/response là JSON UTF-8 kết thúc newline, giới hạn 4 MiB cho một frame; RPC có timeout. TCP không bảo đảm một recv nhận đủ một message nên dùng readline trên stream, không giả định ranh giới TCP packet.

| RPC | Mục đích |
| --- | --- |
| ping | Kiểm tra readiness |
| get | Đọc một bản ghi kể cả metadata/tombstone |
| put | Merge bản ghi và ACK nếu trạng thái hiện tại đúng với bản được yêu cầu |
| snapshot | Quan sát toàn bộ store phục vụ demo |
| exchange | Nhận snapshot của peer, merge rồi trả snapshot sau merge |
| sync | Khởi tạo Push-Pull tới peer được chỉ định |
| gossip | Bật/tắt gossip nền để kiểm soát thí nghiệm |

Merge giữ phiên bản cao hơn. Gửi lại cùng phiên bản và cùng nội dung là idempotent. Cùng key, cùng version nhưng khác nội dung bị báo lỗi để phát hiện writer dùng sai giả định. Store lock chỉ giữ lúc snapshot/merge, không giữ khi đợi mạng để tránh deadlock giữa hai replica đồng bộ chéo.

## Quorum và thứ tự ghi

Proxy chỉ nhận cấu hình thỏa `NR+NW>N`, `2*NW>N` và `1<=NR,NW<=N`. Mỗi request chỉ tính tối đa một phiếu từ mỗi node. Đọc lấy NR phản hồi, chọn phiên bản cao nhất, trả None nếu thiếu key hoặc bản mới nhất là tombstone.

Một proxy duy nhất serialize các thao tác bằng RLock. Trước ghi/xóa, proxy lấy NR bản ghi, cấp `version = max(local_counter, observed_versions)+1`, gửi cùng record đến replica theo thứ tự S1..S5 và dừng khi có NW ACK. Đọc sau một ghi thành công gặp ít nhất một node trong write quorum; nếu không có ghi đồng thời ngoài proxy hoặc mất trạng thái, record mới nhất thắng bản cũ.

Đây là cách điều phối ghi có chủ ý cho lab dùng scalar version. Hai proxy độc lập có thể chọn cùng version; giao nhau của quorum không tự giải quyết trường hợp đó. Muốn hỗ trợ nhiều coordinator cần mở rộng bằng cơ chế khóa/fencing hoặc giao thức điều phối version/consensus phù hợp.

Vì ghi có thêm read-before-write, khả năng hoàn tất ghi của bản triển khai là cần ít nhất `max(NR,NW)` node phản hồi ở các pha tương ứng. Ví dụ cấu hình NR=5,NW=3 vẫn cần đủ 5 node ở pha đọc. Với mặc định 3/3, chịu 2 node dừng. Gửi RPC tuần tự giúp dễ theo dõi log nhưng độ trễ cao hơn gửi song song, đặc biệt khi có node không phản hồi.

## Push-Pull và tombstone

Mỗi replica mặc định đợi 2 giây giữa các lượt chọn peer. Một lượt gồm snapshot cục bộ -> exchange tới peer -> peer merge -> trả snapshot mới -> merge tại bên khởi tạo. Lịch wall-clock còn chịu thời gian RPC và scheduler; đây không phải hệ thống thời gian thực. Khi peer chết, ghi log rồi thử peer ngẫu nhiên ở lượt sau. Nếu mạng cuối cùng liên thông và tiếp tục có các trao đổi thành công, các phiên bản mới sẽ lan ra cụm.

Xóa dùng cùng write quorum với record `{value:null,version_number:v+1,is_tombstone:true}`. Đọc thấy tombstone mới nhất trả None. Không purge tombstone vì lab không có bound thời gian offline và không có cơ chế chứng minh mọi replica đã nhận xóa. Ghi mới sau xóa với version cao hơn tái tạo key hợp lệ.

## Lỗi, phạm vi bảo đảm và giới hạn

- Thiếu NR: báo QuorumError trước khi có thể trả dữ liệu. Ghi thiếu NR ở pha chuẩn bị chưa gửi put.
- Đã gửi put nhưng thiếu NW ACK: báo write indeterminate, vì một số node có thể đã áp dụng và gossip tiếp. Không rollback giả và không tuyên bố ghi thất bại đồng nghĩa chưa có tác dụng.
- Node bị terminate mất RAM. Lab không có WAL, snapshot ra đĩa, recovery sau restart hoặc tái gia nhập process mới; demo stale rejoin là một node còn giữ bản cũ tiếp tục trao đổi. Không hỗ trợ Byzantine hoặc partition có thể cấu hình tùy ý.
- Proxy là một điểm điều phối; không có failover proxy trong khi thao tác đang chạy. Phần gossip là peer-to-peer và vẫn hoạt động độc lập.
- Không gọi đây là cài đặt đầy đủ Gifford có lock phân tán, Raft, Paxos hoặc linearizable register nhiều writer. Quorum và version đủ cho những lịch thực thi nằm trong giả định một proxy, ghi tuần tự của bài lab.
- Full-state gossip đơn giản, chi phí tỷ lệ kích thước store mỗi exchange. Hệ thống lớn cần digest/delta, giới hạn tài nguyên và persistence. Đây là lab localhost, không phải server công khai có xác thực.

## Đối chiếu rubric

| Mục | Tỷ trọng trong đề | Bằng chứng |
| --- | --- | --- |
| Gifford quorum | 35% | validate_quorums; Proxy.read/write/delete; kiểm thử cấu hình, phiên bản cao nhất, node lỗi |
| Anti-entropy | 30% | Replica.sync/gossip_loop; kiểm thử hai chiều và hội tụ tự động qua TCP |
| Tombstone | 25% | Store.merge; delete; kiểm thử stale update không hồi sinh, tái tạo, hội tụ bản xóa |
| Documentation | 10% | README, tóm tắt/lời giải, báo cáo này, execution_trace và log từng server |
| 5 process và socket | Yêu cầu kiến trúc | Cluster.start dùng spawn; trace ghi 5 PID và 5 TCP endpoint |

Đây là đối chiếu yêu cầu, không phải cam kết điểm số của giảng viên.

## Kiểm chứng

`python3 -m unittest -v` chạy kiểm thử dữ liệu thuần và TCP integration thật. Test background gossip sử dụng interval mặc định 2 giây, deadline 16 giây và kiểm tra 5 PID khác nhau. `python3 run_demo.py` kiểm tra kết quả ở từng bước bằng require/AssertionError, vẫn hoạt động khi bật python -O, ghi trace và log từng server. Chạy xong context manager dừng và join các process.

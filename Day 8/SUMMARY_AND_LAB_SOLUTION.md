# Day 8: Tóm tắt bài giảng và lời giải lab

Nguồn: `Lect08_consistency_protocols and quorum_systems.pdf`, 46 trang PDF. Số trang bên dưới là trang trong file PDF; số slide khác vì có các trang phân chia nội dung. Đề lab: trang 41–43, slide 36–38.

## 1. Bức tranh tổng thể

Replication tạo nhiều bản sao để giảm độ trễ, chia tải và duy trì khả năng phục vụ khi có lỗi. Đổi lại, hệ thống phải quyết định đặt bản sao ở đâu, lan truyền thay đổi thế nào và khi nào có thể trả lời đọc/ghi. Bài này đi từ quản lý replica đến các giao thức primary-backup, active replication, quorum và gossip.

## 2. Quản lý replica và lan truyền dữ liệu (trang 4–9)

**Server placement** quyết định vị trí máy chủ trong dài hạn. **Content placement** quyết định dữ liệu nào nằm ở đâu, có thể thay đổi theo lưu lượng.

Với bài toán chọn K vị trí trong N ứng viên, phương pháp greedy chọn vị trí giảm độ trễ trung bình nhiều nhất ở từng bước. Đây là heuristic, không đảm bảo nghiệm tối ưu toàn cục. Khi thêm server, đánh giá khoảng cách tới server gần nhất của mỗi nhóm client.

Ba loại bản sao:

| Loại | Ai tạo/quản lý | Ví dụ |
| --- | --- | --- |
| Permanent | Quản trị viên | Database mirror ở trung tâm dữ liệu |
| Server-initiated | Server tự quyết định | CDN tạo edge replica khi khu vực tăng tải |
| Client-initiated | Client | Cache trình duyệt, ứng dụng |

Dùng hai ngưỡng `D_high > D_low`: tạo bản sao khi nhu cầu vượt ngưỡng cao; bỏ khi xuống dưới ngưỡng thấp. Khoảng cách giữa hai ngưỡng giúp tránh tạo/xóa liên tục khi nhu cầu dao động.

Payload cập nhật có thể là:

- **Invalidation:** gửi thông báo vô hiệu; lần đọc tiếp theo phải tải lại. Phù hợp ghi nhiều, đọc ít.
- **Full state:** gửi toàn bộ giá trị mới. Phù hợp đọc nhiều, ít cập nhật.
- **Operation:** gửi lệnh nhỏ như `add(5)` thay cho đối tượng lớn; phải bảo đảm thứ tự và cách xử lý lặp lại phù hợp.

**Push:** phía nguồn chủ động gửi thay đổi. **Pull:** replica hỏi khi đọc hoặc hết TTL. Push giảm khoảng thời gian stale nhưng tốn lưu lượng dù dữ liệu không được đọc; pull có thể thêm độ trễ tải lại. Push tự nó không phải chứng minh strong consistency: vẫn cần quy tắc ACK và phục vụ đọc.

## 3. Epidemic gossip và tombstone (trang 11–18)

Mỗi node chọn ngẫu nhiên peer để trao đổi. Với một cập nhật, node có thể là susceptible (chưa biết), infective (biết và lan truyền), removed (biết nhưng ngừng lan truyền).

- **Push:** bên khởi tạo gửi dữ liệu sang peer.
- **Pull:** bên khởi tạo lấy dữ liệu từ peer.
- **Push-Pull:** hai bên trao đổi và hợp nhất dữ liệu còn thiếu.

Anti-entropy chạy định kỳ để sửa sai khác. Rumor spreading chỉ tích cực lan tin mới; khi gặp peer đã biết, bên gửi có thể ngừng với xác suất `1/k`. Vì có thể dừng quá sớm nên rumor có nguy cơ để lại node chưa nhận tin.

Trong mô hình giao tiếp ngẫu nhiên thuận lợi, thời gian hội tụ tăng theo `O(log N)`. Đây là ước lượng xác suất, không phải bảo đảm mọi node nhận dữ liệu sau một số vòng cố định khi mạng lỗi hoặc phân vùng.

**Erasure paradox:** A xóa hẳn bản ghi, B vẫn còn bản cũ; lần đồng bộ sau, A tưởng mình thiếu dữ liệu nên nhận lại bản của B. Giải pháp là lưu một bản ghi xóa có phiên bản mới hơn:

```text
Trước: user1 -> {value: "Bob", version_number: 1, is_tombstone: false}
Sau:   user1 -> {value: null,  version_number: 2, is_tombstone: true}
```

Tombstone cũng được gossip như dữ liệu thường. Phiên bản 1 không thể ghi đè phiên bản 2. Chỉ thu gom tombstone khi có cơ sở rằng replica cũ không thể đưa dữ liệu trở lại; TTL tùy ý không đủ nếu một node offline vô hạn.

CDN dùng cache gần người dùng, kết hợp TTL/pull với push invalidation. Anycast cho nhiều điểm mạng quảng bá cùng IP; BGP chọn đường theo chính sách và topology, không bảo đảm server gần nhất về địa lý hay độ trễ đo được thấp nhất.

## 4. Primary và active replication (trang 20–25)

| Giao thức | Cách ghi | Ưu điểm và chi phí |
| --- | --- | --- |
| Remote-write primary-backup | Client gửi primary; primary cập nhật backup và đợi ACK theo chính sách | Một nơi sắp thứ tự ghi; độ trễ phụ thuộc backup phải chờ |
| Local-write / primary migration | Chuyển quyền primary và trạng thái mới nhất đến nơi ghi | Ghi tại chỗ thuận tiện; phải kiểm soát chuyển quyền để tránh hai primary |
| Active replication / SMR | Mọi replica thực thi cùng thao tác theo cùng tổng thứ tự | Cần hàm chuyển trạng thái xác định và giao thức sắp thứ tự |

Active replication yêu cầu cùng đầu vào và trạng thái cũ tạo cùng kết quả. Đọc đồng hồ cục bộ hoặc dùng ngẫu nhiên độc lập sẽ làm trạng thái lệch nhau; nếu cần, giá trị đó phải được đưa vào đầu vào đã thống nhất. Lamport timestamp giúp sắp xếp sự kiện nhưng không tự cung cấp đầy đủ total-order delivery hay consensus trong mạng có lỗi.

Lưu ý khi đọc bảng trang 35: mô tả primary-backup chịu `N−1` lỗi backup không đồng nghĩa vẫn ghi được nếu chính sách trang 20 bắt buộc đợi ACK của **tất cả** backup. Phải phân biệt còn dữ liệu với còn khả năng hoàn tất ghi.

## 5. Gifford quorum (trang 26–27, 33, 37–38)

Trong lab, mỗi node có một phiếu, tổng N node. Cấu hình phải thỏa:

```text
1 <= NR, NW <= N
NR + NW > N
2 * NW > N
```

Điều kiện thứ nhất về giao nhau bảo đảm read quorum gặp ít nhất một node của write quorum. Điều kiện thứ hai bảo đảm hai write quorum giao nhau. Với phiếu trọng số khác nhau, công thức phải dùng tổng trọng số; lab sử dụng phiếu bằng nhau.

**Đọc:** thu đủ NR phản hồi, chọn bản ghi có `version_number` cao nhất. Nếu bản ghi đó là tombstone thì trả không tồn tại. Không chọn giá trị xuất hiện nhiều nhất trong các phản hồi.

**Ghi:** chỉ thông báo committed khi nhận ít nhất NW ACK cho cùng bản ghi mới. Bất đẳng thức quorum là điều kiện giao nhau; vẫn cần quy tắc cấp phiên bản và điều phối ghi để tránh xung đột concurrent writers.

Với N=5:

| NR | NW | Đặc điểm |
| --- | --- | --- |
| 3 | 3 | Cân bằng; đọc hoặc ghi trực tiếp cần ba node |
| 2 | 4 | Đọc cần ít node, ghi cần nhiều node |
| 1 | 5 | Đọc một node, ghi phải tới tất cả |
| 2 | 3 | Không hợp lệ vì tổng bằng N |
| 4 | 2 | Không hợp lệ vì write quorum không quá bán |

Cassandra trong slide minh họa các mức ONE, QUORUM, ALL, LOCAL_QUORUM. Với mức quorum, phía phân vùng không đủ số phiếu phải chờ hoặc báo lỗi; quorum không bảo đảm mọi phía của partition đều tiếp tục ghi.

## 6. Cache coherence và web caching (trang 29–34)

- **Invalidate vs update:** invalidate tiết kiệm payload khi ghi nhiều/ít đọc; update gửi giá trị mới để tránh cache miss khi đọc nhiều.
- **Write-through:** ghi đồng bộ về store trước khi trả thành công, chậm hơn nhưng giảm rủi ro mất dữ liệu chưa flush.
- **Write-back:** trả sau khi cập nhật cache; flush nền nhanh hơn nhưng có thể mất cập nhật nếu cache chết.
- **Conditional GET:** `If-Modified-Since`, đáp ứng `304 Not Modified` để tái dùng cache hoặc `200 OK` kèm nội dung mới.
- **Hierarchical cache:** cache miss đi lên tầng cha. **Cooperative cache:** hỏi các peer; Bloom-filter digest có thể giảm truy vấn nhưng có false positive.
- Bigtable/Chubby minh họa phối hợp dữ liệu theo phạm vi và dịch vụ khóa/điều phối dựa trên consensus.

## 7. Giải câu hỏi ôn tập (trang 38–40)

### N=7, NR=3, NW=5

`3+5=8>7` và `5>7/2=3.5`: cấu hình hợp lệ. Đọc thuần quorum chịu tối đa `7−3=4` node không phản hồi; ghi trực tiếp chịu `7−5=2` node không phản hồi. Các con số giả sử các node còn lại có thể liên lạc và bỏ qua yêu cầu bổ sung của giao thức.

### 1.024 node Push-Pull

Theo phép xấp xỉ của slide: `R ≈ log2(1024) = 10 vòng`. Không diễn giải thành deadline chắc chắn: chọn trùng peer, lỗi mạng và lịch liên lạc có thể làm hội tụ lâu hơn.

### Vì sao Push-Pull tốt hơn Push-only ở giai đoạn cuối?

Khi gần hết node đã có dữ liệu, các lần push thường gửi tới node đã biết nên lãng phí. Node còn thiếu khi chủ động chọn peer để pull có xác suất cao gặp node đã biết. Push-Pull tận dụng cả hướng gửi và hướng lấy.

### k=2 trong rumor spreading

Xác suất ngừng **khi gặp peer đã biết** là `1/2 = 50%`, không phải 50% node toàn mạng ngừng mỗi vòng. Slide nêu kết quả trên 98% nhận tin, dưới 2% residue; không thể suy ra một tỷ lệ residue chính xác cho mọi topology chỉ từ k, khi chưa có mô hình và điều kiện thí nghiệm.

### Vì sao active replication phải deterministic?

Mọi replica tự thực thi cùng lệnh; khác kết quả sẽ gây divergence. Primary-backup có thể thực thi trên primary rồi truyền kết quả/trạng thái đã tính cho backup, nên không đòi hỏi backup tự tái tạo cùng quyết định ngẫu nhiên.

### Tombstone giải erasure paradox thế nào?

Biến sự vắng mặt không rõ nghĩa thành một cập nhật xóa có phiên bản. Khi đồng bộ, bản xóa mới thắng bản sống cũ và được truyền tiếp cho các node còn lại.

## 8. Lời giải bài lab (trang 41–43)

Đã triển khai trong thư mục này:

1. `cluster.py` tạo 5 **OS process** S1..S5 bằng spawn, mỗi process có TCP listener riêng trên localhost, cổng tự cấp để tránh chiếm cổng cố định.
2. `gossip_quorum.py` lưu `key -> {value, version_number, is_tombstone}`, bảo vệ trạng thái bằng khóa, dùng JSON một dòng cho request/response.
3. `Proxy` kiểm tra NR/NW ngay khi khởi tạo; serialize thao tác client và cấp phiên bản tăng dần. Ghi dùng read quorum trước để biết phiên bản hiện có rồi ghi đến đủ NW ACK; xóa là cùng thuật toán với tombstone.
4. Mỗi replica có thread nền thức dậy sau mỗi 2 giây, chọn peer ngẫu nhiên, gửi snapshot và nhận snapshot sau merge. Cả hai phía chỉ nhận bản mới hơn.
5. Demo chủ động tạm tắt gossip ở đầu để thể hiện rõ dữ liệu stale; sau đó bật lại gossip định kỳ và đợi quan sát đủ 5 tombstone.
6. Tombstone được giữ suốt vòng đời cluster. Node lỗi được mô phỏng bằng dừng process; stale replica được mô phỏng bằng giữ process chưa nhận cập nhật rồi cho trao đổi lại.

Ví dụ: ghi Bob phiên bản 1 trên S1,S2,S3. Đọc S3,S4,S5 vẫn thấy Bob vì phiên bản 1 lớn hơn trạng thái trống. Xóa tạo phiên bản 2. S5 còn Bob phiên bản 1 gửi sang S1 đã có tombstone 2: S1 giữ tombstone và trả nó về S5. Cuối cùng gossip đưa tombstone tới tất cả node.

Chạy theo `README.md`; xem giải thích kiến trúc, giả định và đối chiếu rubric tại `DESIGN_REPORT.md`. Log `execution_trace.log` là kết quả thực thi, không phải log viết tay.

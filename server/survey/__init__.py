"""Kho survey — cơ chế thứ hai, tách hẳn khỏi luồng đọc-hiểu một bài.

Luồng cũ (`server/pipeline.py`) xoay quanh `cached_prefix(doc)`: toàn văn **một**
bài nhét vào system prompt rồi dịch, giải thích, làm slide trên đó. Cách đó tốt
cho việc hiểu kỹ một bài và không dùng lại được cho câu hỏi cần ba mươi bài.

Ở đây đổi hẳn cách tổ chức:

  bóc → cắt đoạn → **ngữ cảnh hoá** → **bóc phiếu** → đánh chỉ mục FTS5
                                                          ↓
                       hỏi → lập kế hoạch → tìm → đọc → chấm thiếu → tìm tiếp
                                                          ↓
                                             tổng hợp có trích dẫn → kiểm chứng

Kho **không dịch bài**: dịch 50 bài là không khả thi về tiền. Thứ thay cho bản
dịch là *phiếu* (`card`) — mỗi bài rút thành ~600 token có cấu trúc, đủ nhỏ để
phiếu của cả kho nằm gọn trong một prompt được cache.
"""

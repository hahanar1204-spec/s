# 포레스쿨 QR 재고관리 v1.5

무역창고와 한국포레스쿨창고를 나눠 관리하는 QR 재고관리 프로그램입니다.

## v1.5 추가 기능

- 창고 페이지 분리: 전체 창고 / 무역창고 / 한국포레스쿨창고
- 자재 등록 시 창고 선택
- 한국포레스쿨창고 재고판에서 판매 버튼 제공
- 판매 수량 입력 시 현재 재고에서 자동 차감
- 한국포레스쿨창고 재고가 100개 미만으로 내려가면 알림 기록 생성
- `KAKAO_WORK_WEBHOOK_URL` 또는 `ALERT_WEBHOOK_URL` 설정 시 외부 웹훅으로 알림 전송
- QR 조회 화면에 창고명, 사진, 현재수량, 위치, 규격, 메모 표시
- 삭제, 숨김, 대표 사진 업로드 기능 유지

## Railway Variables 권장값

```text
ADMIN_PIN=원하는관리자번호
AUTO_OPEN_BROWSER=0
PUBLIC_BASE_URL=https://qr-production-a0f2.up.railway.app
DATA_DIR=/app/data
LOW_STOCK_THRESHOLD=100
KOREA_WAREHOUSE=한국포레스쿨창고
KAKAO_WORK_WEBHOOK_URL=카카오워크_웹훅_URL
```

일반 카카오톡 단톡방은 서버에서 직접 메시지를 보내는 공식 웹훅 방식이 제한적입니다. 카카오워크 대화방 Incoming Webhook 또는 별도 알림 중계 서비스를 사용하는 것을 권장합니다.

## GitHub 업로드

압축을 풀고 ZIP 파일 자체가 아니라 아래 파일들을 저장소 첫 화면에 업로드하세요.

```text
app.py
requirements.txt
Procfile
railway.json
index.html
app.css
app.js
static 폴더
README.md
```

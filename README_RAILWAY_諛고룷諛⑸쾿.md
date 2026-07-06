# Railway 배포 방법

1. GitHub 저장소에 파일 전체를 업로드합니다.
2. Railway에서 해당 GitHub 저장소를 Redeploy합니다.
3. Variables에 아래 값을 넣습니다.

```text
ADMIN_PIN=원하는관리자번호
AUTO_OPEN_BROWSER=0
PUBLIC_BASE_URL=https://qr-production-a0f2.up.railway.app
DATA_DIR=/app/data
LOW_STOCK_THRESHOLD=100
KOREA_WAREHOUSE=한국포레스쿨창고
```

4. 카카오워크/웹훅 알림을 쓰려면 추가로 아래 값을 넣습니다.

```text
KAKAO_WORK_WEBHOOK_URL=발급받은_웹훅_URL
```

5. 재배포 후 관리자 화면에서 한국포레스쿨창고 자재를 등록합니다.
6. 판매 버튼을 누르고 판매수량을 입력하면 재고에서 차감됩니다.
7. 한국포레스쿨창고 재고가 100개 미만으로 내려가면 알림 기록이 생성되고, 웹훅 URL이 있으면 외부 알림으로 전송됩니다.

주의: `DATA_DIR=/app/data`는 테스트용입니다. Railway 재배포/초기화 때 데이터가 사라질 수 있습니다. 실제 장기 사용은 PostgreSQL + 사진 저장소 구조가 더 안전합니다.

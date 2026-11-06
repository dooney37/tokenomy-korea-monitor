# 2027년 3~7월 아시아 금융권 RWA 행사 — 모니터링 키워드 전략

## 근거와 해석

사용자가 첨부한 두 DOCX의 전체 본문·표·하이퍼링크를 추출해 정리했습니다. 2023~2026년의 개최 이력은 **2027년 후보를 찾기 위한 단서**이지 2027년 개최 확정이 아닙니다. 문서의 2027년 일정 주장도 공식 원문을 재확인해야 합니다. 자료 간 중복을 시리즈·지역·주최사 관점으로 묶은 31개 **행사군**이지 31개 확정 행사가 아닙니다.

## 1. 세 겹으로 찾기

### 첫째: 과거 행사 브랜드 + 2027

**A: 금융권 실무 적합성이 높은 전문행사 우선**

Digital Assets Asia / ONCHAIN Bangkok·Saison / Calastone Connect Forum / 臺灣銀行 貴金屬 RWA 論壇 / FIN/SUM / The Capital Summit Tokyo / Pacific Meta Onchain Finance Summit / 코스콤 토큰증권 매칭데이 / 토크노미 코리아 / BTCON RWA Summit / 이데일리 디지털자산포럼 / Liquidity·LTP / CCXAP RWA 라운드테이블 / HKIFA fund tokenisation / Hong Kong Digital Finance Summit / Asia Onchain Finance Summit / CryptoFi Forum / Vietnam RWA Summit / 대만 RWA·스테이블코인 포럼 / 국내 국회·협회·증권사 솔루션 세미나.

**B: 대형 종합행사는 금융 트랙·소속으로 좁히기**

Hong Kong Web3 Festival / Money20/20 Asia / The Asian Banker Summit / TEAMZ / WebX / IVS Crypto / GFTN Forum Japan / Japan ETF Conference / Southeast Asia Blockchain Week / Malaysia Blockchain Week / Philippine Blockchain Week / GM Vietnam / Lujiazui Forum·陆家嘴论坛 / BEYOND Expo Web2+3 / 한국금융미래포럼 / 뉴스웨이 블록체인 비즈니스 포럼.

**C: 과거 기간 밖 행사는 개최월 이동만 낮은 우선순위로 감시**

RWA Summit / DigiAssets APAC / Digital Asset Summit Asia / Consensus Hong Kong Institutional Summit / 이데일리 STO Summit 등. 싱가포르 가을 행사와 홍콩 2월 행사를 3~7월 목록에 미리 넣지 않습니다.

### 둘째: 이름이 바뀌거나 새로 생긴 행사는 주제 + 금융업권 + 도시

- 한국어: `(토큰증권 OR 실물자산 토큰화 OR 펀드 토큰화) (은행 OR 증권 OR 자산운용) (세미나 OR 포럼 OR 컨퍼런스) 2027`
- 영어: `(tokenisation OR tokenization OR onchain finance) (bank OR asset management OR institutional) (conference OR summit OR forum) 2027`
- 일본어: `(セキュリティトークン OR デジタル証券 OR トークン化) (銀行 OR 証券 OR 機関投資家) (サミット OR セミナー OR フォーラム) 2027`
- 중국어 간체: `(现实世界资产 OR 资产代币化 OR 基金代币化) (银行 OR 证券 OR 资管) (峰会 OR 论坛) 2027`
- 중국어 번체: `(實體資產代幣化 OR 基金代幣化 OR 數位證券) (銀行 OR 證券 OR 資產管理) (論壇 OR 研討會) 2027`
- 지역 보강: Seoul / Tokyo / Hong Kong / Taipei / Singapore / Bangkok / Hanoi / Ho Chi Minh / Kuala Lumpur / Manila / Jakarta / Shanghai / Macau 및 현지 표기.
- 현지어 확장 단서: 태국어 `สินทรัพย์ดิจิทัล`, 베트남어 `hội nghị RWA`, `tài sản số`, 인도네시아어 `tokenisasi`, `aset digital`. 이 단어만으로는 행사·금융권 관련성을 확정하지 않습니다.

금융 사업주제: tokenized funds·MMF / tokenized deposits / bonds·private credit / digital securities / custody / DvP·atomic settlement / collateral / fund distribution / stablecoin treasury / gold tokenization / regulation·compliance.

### 셋째: 스피커 공개·변경 신호 + 소속

- 공개: speakers announced, agenda, programme, panel, keynote, registration open / 연사 공개, 발표자, 프로그램 / 登壇者, 講演者, 開催概要 / 演讲嘉宾, 嘉賓, 講者, 議程.
- 변경: cancelled, postponed, rescheduled / 취소, 연기, 일정 변경 / 延期, 中止 / 取消, 延期.
- 문서에 등장한 소속 검색 단서: Citi, J.P. Morgan, HSBC, Fidelity, Franklin Templeton, Baillie Gifford, Standard Chartered, SBI, SCBX, Saison Capital, 미래에셋증권, 메리츠증권, 한국투자신탁운용, NH농협은행, 하나증권, 한국은행 등.
- **이 명단은 2027년 참석자 명단이 아닙니다.** 인물명보다 소속+공식 스피커 페이지를 먼저 찾고, 현직 소속·직함·발표 주제·참석 여부를 확인합니다.

예시 검색식:

```text
"Digital Assets Asia" 2027 (speakers OR agenda)
"Calastone" 2027 (Hong Kong OR Taiwan OR Singapore) tokenisation
"FIN/SUM" 2027 (登壇者 OR tokenization)
2027 "Franklin Templeton" (RWA OR tokenization) (Hong Kong OR Tokyo OR Seoul)
2027 토큰증권 (증권사 OR 자산운용사) (연사 OR 세미나)
```

브랜드 검색은 개최 공지를 폭넓게 찾고, 연사 공개 키워드는 별도 탐색에 활용합니다. 자동 수집기의 정확한 실행 검색식·소스는 `data/monitor-plan.json`이 기준입니다. 본 문서의 모든 예시를 자동 실행하는 것은 아닙니다.

## 2. 과거와 미래를 섞지 않는 규칙

1. 개최일은 문서·기사 게시일과 분리합니다. 개최연도·시작일·종료일을 확인합니다.
2. 지역은 과거 도시에서 추정하지 않습니다. 순회 행사·온라인 행사의 현행 개최국을 따로 확인합니다.
3. FIN/SUM 본행사와 Japan Fintech Week, TEAMZ와 XRP Tokyo, 모행사와 사이드행사를 구분합니다.
4. ONCHAIN Bangkok, Pacific Meta의 도쿄 Onchain Finance Summit, 홍콩 Asia Onchain Finance Summit은 동명 단어만으로 병합하지 않습니다.
5. RWA의 위험가중자산 의미는 제외합니다: risk-weighted assets / 위험가중자산 / 风险加权资产 / 風險加權資產 / リスクアセット.
6. 에어드롭·NFT·게임·가격 전망뿐인 종합 Web3 행사는 금융 트랙 근거가 없으면 우선순위를 낮춥니다.
7. 취소·연기는 삭제하지 말고 해당 상태와 근거를 남깁니다. 검색 실패는 미개최 증거가 아닙니다.
8. 문서 간 날짜 차이와 원문 링크 재사용은 오류 가능성으로 남깁니다. 특히 Pacific Meta의 같은 URL이 다른 시기 행사 설명에도 등장하는 점을 재확인해야 합니다.

## 3. 매일 두 번 읽을 결과의 우선순위

- **먼저:** A급 행사 신규 공지 + 전통 금융기관 현직 스피커 신규 공개
- **다음:** 개최일·개최지 변경, 스피커 추가·삭제, 취소·연기
- **그다음:** B급 행사에 금융권 토큰화·결제 세션 추가
- **분리:** 기간 외·일정 미확정·공식 근거 미확보
- **경고:** 이번에 읽지 못한 출처, 갱신 지연, 2차 기사만 있는 정보

현재 구현은 규칙 기반 수집기입니다. 실제 스피커 추가·삭제를 완전히 비교하려면 사이트별 연사표 파서를 별도로 구축해야 합니다. 현재는 구조화된 `performer` 또는 페이지 변화·뉴스 검색 후보를 제공하고, 불확실한 항목은 기자가 확인하도록 표시합니다.

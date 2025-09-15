-- 공급사 정보
CREATE TABLE `SUPPLIER_LIST` (
  `SEQ` int NOT NULL AUTO_INCREMENT COMMENT '고유 식별자',
  `COMPANY_NAME` varchar(100) NOT NULL COMMENT '공급사 이름',
  `SUPPLIER_CODE` varchar(50) DEFAULT NULL COMMENT 'CAFE24 공급사 코드',
  `SUPPLIER_ID` varchar(100) DEFAULT NULL COMMENT '공급사 ID',
  `SUPPLIER_PW` varchar(100) DEFAULT NULL COMMENT '공급사 PW',
  `SUPPLIER_URL` varchar(255) DEFAULT NULL COMMENT '공급사 URL',
  `MANAGER` varchar(100) DEFAULT NULL COMMENT '담당자 이름',
  `MANAGER_RANK` varchar(50) DEFAULT NULL COMMENT '담당자 직책',
  `NUMBER` varchar(50) DEFAULT NULL COMMENT '담당자 연락처',
  `EMAIL` varchar(255) DEFAULT NULL COMMENT '이메일',
  `STATE_CODE` VARCHAR(4) DEFAULT NULL COMMENT '상태 코드',
  `CHANNEL_ID` VARCHAR(30) DEFAULT NULL COMMENT '채널 ID',
  `CONTRACT_STATUS` VARCHAR(20) DEFAULT NULL COMMENT '계약서 상태',
  `CONTRACT_ID` VARCHAR(100) DEFAULT NULL COMMENT '계약서 ID',
  `CREAT_DATE` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
  `UPDT_DATE` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 일시',
  PRIMARY KEY (`SEQ`),
  UNIQUE KEY uq_supplier_code (`SUPPLIER_CODE`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='공급사 목록 테이블';

-- SupplierList 테이블 확장 예시 (DDL)
ALTER TABLE dbdoogobiz.SUPPLIER_LIST
  ADD COLUMN contract_template VARCHAR(20) NULL COMMENT 'A(단일%)|B(구간%)',
  ADD COLUMN contract_percent DECIMAL(5,2) NULL COMMENT 'A용: 단일 수수료(%)',
  ADD COLUMN contract_threshold BIGINT NULL COMMENT 'B용: 특정 금액(원)',
  ADD COLUMN contract_percent_over DECIMAL(5,2) NULL COMMENT 'B용: 초과 시 %',
  ADD COLUMN contract_percent_under DECIMAL(5,2) NULL COMMENT 'B용: 이하 시 %',
  ADD COLUMN contract_skip TINYINT(1) DEFAULT 0 COMMENT '외부에서 이미 체결(발송 스킵)';

-- Cafe24 웹훅 이벤트 저장 테이블
CREATE TABLE IF NOT EXISTS webhook_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  dedupe_key VARCHAR(128) NOT NULL,         -- webhook_id 없을 때 대비(헤더/바디 해시)
  webhook_id VARCHAR(64) NULL,
  topic VARCHAR(128) NULL,
  sig_verified TINYINT(1) NOT NULL DEFAULT 0,
  received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  body_json LONGTEXT NULL,
  INDEX idx_topic_received_at (topic, received_at),
  UNIQUE KEY uq_dedupe (dedupe_key)
);

-- OAuth 토큰 저장 테이블 (프로바이더별 1행)
CREATE TABLE IF NOT EXISTS OAUTH_TOKEN (
  PROVIDER      VARCHAR(20)  NOT NULL,               -- 예: 'cafe24'
  MALL_ID       VARCHAR(50)  NULL,                   -- abc123 (선택)
  REFRESH_TOKEN TEXT         NOT NULL,               -- 최신 refresh_token
  ACCESS_TOKEN  TEXT         NULL,                   -- 최근 발급 access_token (선택)
  EXPIRES_AT    DATETIME     NULL,                   -- access_token 만료 시각(선택)
  SCOPE         VARCHAR(255) NULL,                   -- 권한 스코프(선택)
  UPDATED_AT    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CREATED_AT    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (PROVIDER)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

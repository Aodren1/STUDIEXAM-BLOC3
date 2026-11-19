-- Schéma source AssurePlus (SQL Server 2019), conforme à l'annexe de l'énoncé.
-- Points d'attention pour l'intégration :
--   AP_BIRTH_DATE    VARCHAR(10)  date stockée en texte (format attendu YYYY-MM-DD, non contrôlé)
--   AP_INCIDENT_DATE VARCHAR(19)  horodatage stocké en texte, sans fuseau horaire
--   AP_MONTHLY_PREMIUM            prime MENSUELLE (AbAssurance stocke une prime ANNUELLE)
--   AP_PAYMENT_REF   UNIQUEIDENTIFIER (GUID) : seule clé non numérique des deux bases

CREATE TABLE AP_USERS (
  AP_USER_ID          INT            PRIMARY KEY,
  AP_FULL_NAME        NVARCHAR(200),
  AP_BIRTH_DATE       VARCHAR(10),
  AP_MAIL_ADDRESS     NVARCHAR(150),
  AP_PHONE_NUMBER     NVARCHAR(25),
  AP_STREET_ADDRESS   NVARCHAR(255),
  AP_ZIP_CODE         NVARCHAR(10),
  AP_CREATED_AT       DATETIME,
  AP_CUSTOMER_STATUS  NVARCHAR(20),
  AP_LOYALTY_SCORE    INT
);

CREATE TABLE AP_CONTRACTS (
  AP_CONTRACT_REF     NVARCHAR(30)   PRIMARY KEY,
  AP_USER_ID          INT,
  AP_PRODUCT_CODE     NVARCHAR(50),
  AP_START_DATE       DATETIME,
  AP_END_DATE         DATETIME,
  AP_MONTHLY_PREMIUM  DECIMAL(10,2),
  AP_CONTRACT_STATE   NVARCHAR(20),
  AP_BROKER_CODE      NVARCHAR(30)
);

CREATE TABLE AP_CLAIMS (
  AP_SINISTRE_NUM     INT            PRIMARY KEY,
  AP_CONTRACT_REF     NVARCHAR(30),
  AP_INCIDENT_DATE    VARCHAR(19),
  AP_ESTIMATED_AMOUNT DECIMAL(12,2),
  AP_CLAIM_STATUS     NVARCHAR(30),
  AP_CLAIM_COMMENT    NVARCHAR(MAX),
  AP_FRAUD_SCORE      DECIMAL(5,2)
);

CREATE TABLE AP_PAYMENTS (
  AP_PAYMENT_REF      UNIQUEIDENTIFIER PRIMARY KEY,
  AP_CONTRACT_REF     NVARCHAR(30),
  AP_PAYMENT_DATETIME DATETIME,
  AP_AMOUNT_PAID      DECIMAL(10,2),
  AP_PAYMENT_CHANNEL  NVARCHAR(30),
  AP_TRANSACTION_STATUS NVARCHAR(20)
);

-- Activation du CDC natif SQL Server (lu par Debezium en cible) :
-- EXEC sys.sp_cdc_enable_db;
-- EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'AP_PAYMENTS', @role_name = NULL;

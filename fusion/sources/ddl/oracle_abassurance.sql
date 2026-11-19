-- Schéma source AbAssurance (Oracle 19c), conforme à l'annexe de l'énoncé.
-- Les contraintes de clé sont celles constatées en production : AB_CONTRAT.AB_CLIENT_ID et les
-- AB_POLICY_NUMBER des tables filles ne sont PAS déclarées en clé étrangère (orphelins possibles).

CREATE TABLE AB_CLIENT (
  AB_CLIENT_ID        NUMBER        PRIMARY KEY,
  AB_NOM              VARCHAR2(100),
  AB_PRENOM           VARCHAR2(100),
  AB_DATE_NAISSANCE   DATE,
  AB_EMAIL            VARCHAR2(150),
  AB_TELEPHONE        VARCHAR2(20),
  AB_ADRESSE          VARCHAR2(255),
  AB_CODE_POSTAL      VARCHAR2(10),
  AB_NUM_FISCAL       VARCHAR2(30),
  AB_DATE_CREATION    TIMESTAMP,
  AB_STATUT_CLIENT    VARCHAR2(20)
);

CREATE TABLE AB_CONTRAT (
  AB_POLICY_NUMBER    VARCHAR2(30)  PRIMARY KEY,
  AB_CLIENT_ID        NUMBER,
  AB_TYPE_ASSURANCE   VARCHAR2(50),
  AB_DATE_DEBUT       DATE,
  AB_DATE_FIN         DATE,
  AB_PRIME_ANNUELLE   NUMBER(10,2),
  AB_STATUT_CONTRAT   VARCHAR2(20),
  AB_AGENCE_ID        NUMBER
);

CREATE TABLE AB_SINISTRE (
  AB_CLAIM_ID         NUMBER        PRIMARY KEY,
  AB_POLICY_NUMBER    VARCHAR2(30),
  AB_DATE_SINISTRE    TIMESTAMP,
  AB_MONTANT_ESTIME   NUMBER(12,2),
  AB_STATUT_SINISTRE  VARCHAR2(30),
  AB_DESCRIPTION      CLOB
);

CREATE TABLE AB_PAIEMENT (
  AB_PAYMENT_ID       NUMBER        PRIMARY KEY,
  AB_POLICY_NUMBER    VARCHAR2(30),
  AB_DATE_PAIEMENT    DATE,
  AB_MONTANT          NUMBER(10,2),
  AB_MODE_PAIEMENT    VARCHAR2(30)
);

-- Colonne technique ajoutée pour l'extraction incrémentale (watermark) sans toucher aux applications :
-- alimentée par trigger, indexée. Alternative retenue en cible : CDC LogMiner via Debezium.
-- ALTER TABLE AB_PAIEMENT ADD (AB_MAJ_TS TIMESTAMP DEFAULT SYSTIMESTAMP);

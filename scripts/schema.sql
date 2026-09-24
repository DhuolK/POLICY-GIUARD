-- =============================================================================
-- POLICYGUARD MySQL Database Schema DDL
-- Target: MySQL 8.0+ / MariaDB 10.4+ (Truehost cPanel Shared Hosting)
-- Engine: InnoDB, Character Set: utf8mb4, Collation: utf8mb4_unicode_ci
-- =============================================================================

SET FOREIGN_KEY_CHECKS = 0;
SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
SET time_zone = "+00:00";

-- -----------------------------------------------------------------------------
-- 1. Table: users
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255) NOT NULL,
  `password_hash` VARCHAR(255) DEFAULT NULL,
  `full_name` VARCHAR(255) NOT NULL,
  `phone` VARCHAR(50) DEFAULT NULL,
  `role` VARCHAR(50) NOT NULL DEFAULT 'customer',
  `created_by` INT UNSIGNED DEFAULT NULL,
  `assigned_worker_id` INT UNSIGNED DEFAULT NULL,
  `failed_login_attempts` INT UNSIGNED NOT NULL DEFAULT 0,
  `locked_until` DATETIME DEFAULT NULL,
  `disabled` TINYINT(1) NOT NULL DEFAULT 0,
  `client_id_number` VARCHAR(50) DEFAULT NULL,
  `kra_pin` VARCHAR(50) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_email` (`email`),
  KEY `idx_users_role` (`role`),
  KEY `idx_users_phone` (`phone`),
  KEY `idx_users_client_id_number` (`client_id_number`),
  KEY `idx_users_kra_pin` (`kra_pin`),
  KEY `idx_users_created_by` (`created_by`),
  KEY `idx_users_assigned_worker_id` (`assigned_worker_id`),
  CONSTRAINT `fk_users_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_users_assigned_worker` FOREIGN KEY (`assigned_worker_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 2. Table: vehicles
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `vehicles`;
CREATE TABLE `vehicles` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `owner_id` INT UNSIGNED NOT NULL,
  `registration_number` VARCHAR(50) NOT NULL,
  `make` VARCHAR(100) NOT NULL,
  `model` VARCHAR(100) NOT NULL,
  `year` INT DEFAULT NULL,
  `category` VARCHAR(50) NOT NULL DEFAULT 'motor',
  `seating_capacity` INT NOT NULL DEFAULT 5,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_vehicles_registration_number` (`registration_number`),
  KEY `idx_vehicles_owner_id` (`owner_id`),
  CONSTRAINT `fk_vehicles_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 3. Table: insurance_companies
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `insurance_companies`;
CREATE TABLE `insurance_companies` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `code` VARCHAR(50) NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `ira_license_number` VARCHAR(100) DEFAULT NULL,
  `contact_email` VARCHAR(255) DEFAULT NULL,
  `contact_phone` VARCHAR(50) DEFAULT NULL,
  `commission_rate` DECIMAL(5, 2) NOT NULL DEFAULT 0.00,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_insurance_companies_code` (`code`),
  UNIQUE KEY `uq_insurance_companies_ira` (`ira_license_number`),
  KEY `idx_insurance_companies_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 4. Table: policy_types
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `policy_types`;
CREATE TABLE `policy_types` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `slug` VARCHAR(100) NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `category` VARCHAR(50) NOT NULL DEFAULT 'motor',
  `description` TEXT DEFAULT NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_policy_types_slug` (`slug`),
  KEY `idx_policy_types_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 5. Table: policies
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `policies`;
CREATE TABLE `policies` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `policy_number` VARCHAR(100) NOT NULL,
  `client_id` INT UNSIGNED NOT NULL,
  `vehicle_id` INT UNSIGNED DEFAULT NULL,
  `insurance_company_id` INT UNSIGNED NOT NULL,
  `policy_type_id` INT UNSIGNED NOT NULL,
  `status` VARCHAR(50) NOT NULL DEFAULT 'draft',
  `sum_insured` DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
  `premium` DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
  `seating_capacity` INT DEFAULT NULL,
  `start_date` DATETIME DEFAULT NULL,
  `end_date` DATETIME DEFAULT NULL,
  `created_by` INT UNSIGNED DEFAULT NULL,
  `assigned_worker_id` INT UNSIGNED DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_policies_policy_number` (`policy_number`),
  KEY `idx_policies_client_id` (`client_id`),
  KEY `idx_policies_vehicle_id` (`vehicle_id`),
  KEY `idx_policies_company_id` (`insurance_company_id`),
  KEY `idx_policies_type_id` (`policy_type_id`),
  KEY `idx_policies_status` (`status`),
  KEY `idx_policies_end_date` (`end_date`),
  KEY `idx_policies_created_at` (`created_at`),
  CONSTRAINT `fk_policies_client` FOREIGN KEY (`client_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_policies_vehicle` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_policies_company` FOREIGN KEY (`insurance_company_id`) REFERENCES `insurance_companies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_policies_type` FOREIGN KEY (`policy_type_id`) REFERENCES `policy_types` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_policies_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_policies_assigned_worker` FOREIGN KEY (`assigned_worker_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 6. Table: policy_versions
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `policy_versions`;
CREATE TABLE `policy_versions` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `policy_id` INT UNSIGNED NOT NULL,
  `version_number` INT UNSIGNED NOT NULL,
  `snapshot_json` LONGTEXT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_policy_version` (`policy_id`, `version_number`),
  KEY `idx_policy_versions_policy_id` (`policy_id`),
  CONSTRAINT `fk_policy_versions_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 7. Table: claims
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `claims`;
CREATE TABLE `claims` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `claim_number` VARCHAR(100) NOT NULL,
  `policy_id` INT UNSIGNED NOT NULL,
  `client_id` INT UNSIGNED NOT NULL,
  `incident_date` DATETIME NOT NULL,
  `report_date` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` VARCHAR(50) NOT NULL DEFAULT 'reported',
  `description` TEXT DEFAULT NULL,
  `estimated_amount` DECIMAL(14, 2) NOT NULL DEFAULT 0.00,
  `settled_amount` DECIMAL(14, 2) DEFAULT NULL,
  `fraud_status` VARCHAR(50) NOT NULL DEFAULT 'clean',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_claims_claim_number` (`claim_number`),
  KEY `idx_claims_policy_id` (`policy_id`),
  KEY `idx_claims_client_id` (`client_id`),
  KEY `idx_claims_status` (`status`),
  KEY `idx_claims_created_at` (`created_at`),
  CONSTRAINT `fk_claims_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_claims_client` FOREIGN KEY (`client_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 8. Table: payments
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `payments`;
CREATE TABLE `payments` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `receipt_number` VARCHAR(100) NOT NULL,
  `policy_id` INT UNSIGNED NOT NULL,
  `client_id` INT UNSIGNED NOT NULL,
  `amount` DECIMAL(14, 2) NOT NULL,
  `payment_date` DATETIME DEFAULT NULL,
  `status` VARCHAR(50) NOT NULL DEFAULT 'pending',
  `payment_method` VARCHAR(50) NOT NULL DEFAULT 'mpesa',
  `mpesa_trans_id` VARCHAR(100) DEFAULT NULL,
  `notes` TEXT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_payments_receipt_number` (`receipt_number`),
  KEY `idx_payments_policy_id` (`policy_id`),
  KEY `idx_payments_client_id` (`client_id`),
  KEY `idx_payments_status` (`status`),
  KEY `idx_payments_mpesa_trans_id` (`mpesa_trans_id`),
  KEY `idx_payments_created_at` (`created_at`),
  CONSTRAINT `fk_payments_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_payments_client` FOREIGN KEY (`client_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 9. Table: mpesa_transactions
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `mpesa_transactions`;
CREATE TABLE `mpesa_transactions` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `trans_id` VARCHAR(100) NOT NULL,
  `trans_time` DATETIME NOT NULL,
  `trans_amount` DECIMAL(14, 2) NOT NULL,
  `business_short_code` VARCHAR(50) NOT NULL,
  `bill_ref_number` VARCHAR(100) DEFAULT NULL,
  `invoice_number` VARCHAR(100) DEFAULT NULL,
  `org_account_balance` DECIMAL(14, 2) DEFAULT NULL,
  `third_party_trans_id` VARCHAR(100) DEFAULT NULL,
  `msisdn` VARCHAR(50) NOT NULL,
  `first_name` VARCHAR(100) DEFAULT NULL,
  `middle_name` VARCHAR(100) DEFAULT NULL,
  `last_name` VARCHAR(100) DEFAULT NULL,
  `status` VARCHAR(50) NOT NULL DEFAULT 'pending',
  `raw_payload` LONGTEXT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_mpesa_transactions_trans_id` (`trans_id`),
  KEY `idx_mpesa_transactions_msisdn` (`msisdn`),
  KEY `idx_mpesa_transactions_bill_ref` (`bill_ref_number`),
  KEY `idx_mpesa_transactions_status` (`status`),
  KEY `idx_mpesa_transactions_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 10. Table: reminders
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `reminders`;
CREATE TABLE `reminders` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `policy_id` INT UNSIGNED NOT NULL,
  `client_id` INT UNSIGNED NOT NULL,
  `reminder_type` VARCHAR(50) NOT NULL,
  `due_date` DATETIME NOT NULL,
  `status` VARCHAR(50) NOT NULL DEFAULT 'pending',
  `sent_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_reminders_policy_id` (`policy_id`),
  KEY `idx_reminders_client_id` (`client_id`),
  KEY `idx_reminders_due_date` (`due_date`),
  KEY `idx_reminders_status` (`status`),
  CONSTRAINT `fk_reminders_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_reminders_client` FOREIGN KEY (`client_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 11. Table: sms_outbox
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sms_outbox`;
CREATE TABLE `sms_outbox` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `phone_number` VARCHAR(50) NOT NULL,
  `destination` VARCHAR(50) NOT NULL,
  `message` TEXT NOT NULL,
  `kind` VARCHAR(50) NOT NULL,
  `priority` INT NOT NULL DEFAULT 1,
  `status` VARCHAR(50) NOT NULL DEFAULT 'queued',
  `idempotency_key` VARCHAR(128) NOT NULL,
  `template_key` VARCHAR(100) DEFAULT NULL,
  `template_version` INT NOT NULL DEFAULT 1,
  `policy_id` INT UNSIGNED DEFAULT NULL,
  `client_id` INT UNSIGNED DEFAULT NULL,
  `manual` TINYINT(1) NOT NULL DEFAULT 0,
  `meta` LONGTEXT DEFAULT NULL,
  `attempts` INT NOT NULL DEFAULT 0,
  `max_attempts` INT NOT NULL DEFAULT 5,
  `simulated` TINYINT(1) NOT NULL DEFAULT 0,
  `segments` INT NOT NULL DEFAULT 0,
  `cost_kes` DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
  `provider_ref` VARCHAR(100) DEFAULT NULL,
  `provider_status` VARCHAR(50) DEFAULT NULL,
  `last_error` TEXT DEFAULT NULL,
  `next_attempt_at` DATETIME DEFAULT NULL,
  `deferred_reason` VARCHAR(100) DEFAULT NULL,
  `suppress_reason` VARCHAR(100) DEFAULT NULL,
  `worker` VARCHAR(100) DEFAULT NULL,
  `sent_at` DATETIME DEFAULT NULL,
  `delivered_at` DATETIME DEFAULT NULL,
  `dead_at` DATETIME DEFAULT NULL,
  `dead_reason` VARCHAR(100) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sms_outbox_idempotency_key` (`idempotency_key`),
  KEY `idx_sms_outbox_phone_number` (`phone_number`),
  KEY `idx_sms_outbox_destination` (`destination`),
  KEY `idx_sms_outbox_status` (`status`),
  KEY `idx_sms_outbox_policy_id` (`policy_id`),
  KEY `idx_sms_outbox_client_id` (`client_id`),
  KEY `idx_sms_outbox_created_at` (`created_at`),
  CONSTRAINT `fk_sms_outbox_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_sms_outbox_client` FOREIGN KEY (`client_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 12. Table: sms_suppressions
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sms_suppressions`;
CREATE TABLE `sms_suppressions` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `phone_number` VARCHAR(50) NOT NULL,
  `reason` VARCHAR(255) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sms_suppressions_phone` (`phone_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 13. Table: notifications
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `notifications`;
CREATE TABLE `notifications` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NOT NULL,
  `audience` VARCHAR(50) NOT NULL DEFAULT 'staff',
  `category` VARCHAR(50) NOT NULL DEFAULT 'system',
  `severity` VARCHAR(20) NOT NULL DEFAULT 'info',
  `title` VARCHAR(255) NOT NULL,
  `body` TEXT NOT NULL,
  `is_read` TINYINT(1) NOT NULL DEFAULT 0,
  `read_by` TEXT DEFAULT NULL,
  `policy_id` INT UNSIGNED DEFAULT NULL,
  `policy_number` VARCHAR(100) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_notifications_user_id` (`user_id`),
  KEY `idx_notifications_audience` (`audience`),
  KEY `idx_notifications_category` (`category`),
  KEY `idx_notifications_severity` (`severity`),
  KEY `idx_notifications_is_read` (`is_read`),
  KEY `idx_notifications_policy_id` (`policy_id`),
  KEY `idx_notifications_created_at` (`created_at`),
  CONSTRAINT `fk_notifications_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notifications_policy` FOREIGN KEY (`policy_id`) REFERENCES `policies` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 14. Table: audit_logs
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `audit_logs`;
CREATE TABLE `audit_logs` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `performed_by` INT UNSIGNED DEFAULT NULL,
  `action` VARCHAR(100) NOT NULL,
  `entity_type` VARCHAR(100) NOT NULL,
  `entity_id` VARCHAR(100) DEFAULT NULL,
  `details` LONGTEXT DEFAULT NULL,
  `ip_address` VARCHAR(50) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_audit_logs_user_id` (`performed_by`),
  KEY `idx_audit_logs_action` (`action`),
  KEY `idx_audit_logs_entity` (`entity_type`, `entity_id`),
  KEY `idx_audit_logs_created_at` (`created_at`),
  CONSTRAINT `fk_audit_logs_user` FOREIGN KEY (`performed_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 15. Table: app_settings
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `app_settings`;
CREATE TABLE `app_settings` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `key` VARCHAR(100) NOT NULL,
  `value` LONGTEXT DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_app_settings_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 16. Table: counters
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `counters`;
CREATE TABLE `counters` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) NOT NULL,
  `current_val` BIGINT NOT NULL DEFAULT 0,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_counters_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 17. Table: sms_templates
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `sms_templates`;
CREATE TABLE `sms_templates` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `key` VARCHAR(100) NOT NULL,
  `body` TEXT NOT NULL,
  `version` INT NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sms_templates_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

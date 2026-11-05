CREATE TABLE IF NOT EXISTS `dw_workflows` (
  `workflow_id`       VARCHAR(100) NOT NULL,
  `name`              VARCHAR(200) NULL,
  `description`       VARCHAR(2000) NULL,
  `status`            VARCHAR(20)  NULL,
  `enabled`           INT          NULL,
  `schedule_type`     VARCHAR(20)  NULL,
  `schedule_cron`     VARCHAR(100) NULL,
  `schedule_once_at`  VARCHAR(50)  NULL,
  `owner`             VARCHAR(50)  NULL,
  `created_at`        VARCHAR(30)  NULL,
  `updated_at`        VARCHAR(30)  NULL,
  PRIMARY KEY (`workflow_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

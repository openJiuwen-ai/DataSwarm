CREATE TABLE IF NOT EXISTS `dw_task_instances` (
  `instance_id`    VARCHAR(100) NOT NULL,
  `ref_type`       VARCHAR(20)  NULL,
  `ref_id`         VARCHAR(100) NULL,
  `trigger_type`   VARCHAR(20)  NULL,
  `status`         VARCHAR(20)  NULL,
  `started_at`     VARCHAR(30)  NULL,
  `finished_at`    VARCHAR(30)  NULL,
  `artifact_dir`   VARCHAR(500) NULL,
  `error_message`  TEXT       NULL,
  `created_at`     VARCHAR(30)  NULL,
  PRIMARY KEY (`instance_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

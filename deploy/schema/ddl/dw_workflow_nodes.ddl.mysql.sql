CREATE TABLE IF NOT EXISTS `dw_workflow_nodes` (
  `node_id`         VARCHAR(100) NOT NULL,
  `workflow_id`     VARCHAR(100) NULL,
  `task_id`         VARCHAR(100) NULL,
  `group_name`      VARCHAR(100) NULL,
  `node_name`       VARCHAR(200) NULL,
  `upstream_nodes`  VARCHAR(500) NULL,
  `wait_review`     INT          NULL,
  `order_index`     INT          NULL,
  `created_at`      VARCHAR(30)  NULL,
  PRIMARY KEY (`node_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

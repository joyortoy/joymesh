CREATE TABLE `audit_events` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_email` text NOT NULL,
	`action` text NOT NULL,
	`metadata_json` text DEFAULT '{}' NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `onboarding_progress` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_email` text NOT NULL,
	`state` text DEFAULT 'ACCOUNT_READY' NOT NULL,
	`selected_harnesses_json` text DEFAULT '[]' NOT NULL,
	`node_name` text DEFAULT '' NOT NULL,
	`node_online` integer DEFAULT false NOT NULL,
	`environment_checked` integer DEFAULT false NOT NULL,
	`paid_policy` text DEFAULT 'ask' NOT NULL,
	`fireconnect` integer DEFAULT false NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `onboarding_owner_email_idx` ON `onboarding_progress` (`owner_email`);
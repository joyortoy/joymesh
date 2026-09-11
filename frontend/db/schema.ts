import { integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const onboardingProgress = sqliteTable(
  "onboarding_progress",
  {
    id: text("id").primaryKey(),
    ownerEmail: text("owner_email").notNull(),
    state: text("state").notNull().default("ACCOUNT_READY"),
    selectedHarnessesJson: text("selected_harnesses_json").notNull().default("[]"),
    nodeName: text("node_name").notNull().default(""),
    nodeOnline: integer("node_online", { mode: "boolean" }).notNull().default(false),
    environmentChecked: integer("environment_checked", { mode: "boolean" }).notNull().default(false),
    paidPolicy: text("paid_policy").notNull().default("ask"),
    fireconnect: integer("fireconnect", { mode: "boolean" }).notNull().default(false),
    updatedAt: text("updated_at").notNull(),
  },
  (table) => [uniqueIndex("onboarding_owner_email_idx").on(table.ownerEmail)],
);

export const auditEvents = sqliteTable("audit_events", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  action: text("action").notNull(),
  metadataJson: text("metadata_json").notNull().default("{}"),
  createdAt: text("created_at").notNull(),
});

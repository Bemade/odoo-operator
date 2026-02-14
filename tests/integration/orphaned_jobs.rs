use k8s_openapi::api::batch::v1::Job;
use kube::api::{Api, DeleteParams, PostParams};
use serde_json::json;

use super::common::*;
use odoo_operator::crd::odoo_backup_job::OdooBackupJob;
use odoo_operator::crd::odoo_init_job::OdooInitJob;
use odoo_operator::crd::odoo_instance::OdooInstancePhase;
use odoo_operator::crd::odoo_restore_job::OdooRestoreJob;
use odoo_operator::crd::odoo_upgrade_job::OdooUpgradeJob;

/// BackingUp → Running when the OdooBackupJob CR is deleted mid-backup.
#[tokio::test]
async fn backup_job_orphaned_recovers_to_running() {
    let ctx = TestContext::new("test-bk-orphan").await;
    let (c, ns) = (&ctx.client, ctx.ns.as_str());

    let ready_handle = fast_track_to_running(&ctx, "test-bk-orphan-init").await;

    // Create OdooBackupJob → BackingUp.
    let backup_api: Api<OdooBackupJob> = Api::namespaced(c.clone(), ns);
    let backup_job: OdooBackupJob = serde_json::from_value(json!({
        "apiVersion": "bemade.org/v1alpha1",
        "kind": "OdooBackupJob",
        "metadata": { "name": "test-bk-orphan-job", "namespace": ns },
        "spec": {
            "odooInstanceRef": { "name": "test-bk-orphan" },
            "destination": {
                "bucket": "test-bucket",
                "objectKey": "test-key",
                "endpoint": "http://localhost:9000",
            },
        }
    }))
    .unwrap();
    backup_api
        .create(&PostParams::default(), &backup_job)
        .await
        .expect("failed to create OdooBackupJob");

    assert!(
        wait_for_phase(c, ns, "test-bk-orphan", OdooInstancePhase::BackingUp).await,
        "expected BackingUp after backup job created"
    );

    // Delete the OdooBackupJob CR — simulates user deleting it.
    backup_api
        .delete("test-bk-orphan-job", &DeleteParams::default())
        .await
        .expect("failed to delete OdooBackupJob");

    // Instance should recover to Running (deployment is still ready).
    assert!(
        wait_for_phase(c, ns, "test-bk-orphan", OdooInstancePhase::Running).await,
        "expected Running after orphaned backup job"
    );

    ready_handle.abort();
}

/// Upgrading → Starting when the OdooUpgradeJob CR is deleted mid-upgrade.
#[tokio::test]
async fn upgrade_job_orphaned_recovers_to_starting() {
    let ctx = TestContext::new("test-up-orphan").await;
    let (c, ns) = (&ctx.client, ctx.ns.as_str());

    let ready_handle = fast_track_to_running(&ctx, "test-up-orphan-init").await;

    // Stop faking readyReplicas so we land in Starting (not Running).
    ready_handle.abort();
    fake_deployment_ready(c, ns, "test-up-orphan", 0).await;

    // Create OdooUpgradeJob → Upgrading.
    let upgrade_api: Api<OdooUpgradeJob> = Api::namespaced(c.clone(), ns);
    let upgrade_job: OdooUpgradeJob = serde_json::from_value(json!({
        "apiVersion": "bemade.org/v1alpha1",
        "kind": "OdooUpgradeJob",
        "metadata": { "name": "test-up-orphan-job", "namespace": ns },
        "spec": {
            "odooInstanceRef": { "name": "test-up-orphan" },
            "modules": ["base"],
        }
    }))
    .unwrap();
    upgrade_api
        .create(&PostParams::default(), &upgrade_job)
        .await
        .expect("failed to create OdooUpgradeJob");

    assert!(
        wait_for_phase(c, ns, "test-up-orphan", OdooInstancePhase::Upgrading).await,
        "expected Upgrading after upgrade job created"
    );

    // Delete the OdooUpgradeJob CR.
    upgrade_api
        .delete("test-up-orphan-job", &DeleteParams::default())
        .await
        .expect("failed to delete OdooUpgradeJob");

    // Instance should recover to Starting.
    assert!(
        wait_for_phase(c, ns, "test-up-orphan", OdooInstancePhase::Starting).await,
        "expected Starting after orphaned upgrade job"
    );
}

/// Restoring → Starting when the OdooRestoreJob CR is deleted mid-restore.
#[tokio::test]
async fn restore_job_orphaned_recovers_to_starting() {
    let ctx = TestContext::new("test-rs-orphan").await;
    let (c, ns) = (&ctx.client, ctx.ns.as_str());

    assert!(
        wait_for_phase(c, ns, "test-rs-orphan", OdooInstancePhase::Uninitialized).await,
        "expected Uninitialized"
    );

    // Create OdooRestoreJob → Restoring.
    let restore_api: Api<OdooRestoreJob> = Api::namespaced(c.clone(), ns);
    let restore_job: OdooRestoreJob = serde_json::from_value(json!({
        "apiVersion": "bemade.org/v1alpha1",
        "kind": "OdooRestoreJob",
        "metadata": { "name": "test-rs-orphan-job", "namespace": ns },
        "spec": {
            "odooInstanceRef": { "name": "test-rs-orphan" },
            "source": {
                "type": "s3",
                "s3": {
                    "bucket": "test-bucket",
                    "objectKey": "test-key",
                    "endpoint": "http://localhost:9000",
                },
            },
        }
    }))
    .unwrap();
    restore_api
        .create(&PostParams::default(), &restore_job)
        .await
        .expect("failed to create OdooRestoreJob");

    assert!(
        wait_for_phase(c, ns, "test-rs-orphan", OdooInstancePhase::Restoring).await,
        "expected Restoring after restore job created"
    );

    // Delete the OdooRestoreJob CR.
    restore_api
        .delete("test-rs-orphan-job", &DeleteParams::default())
        .await
        .expect("failed to delete OdooRestoreJob");

    // Instance should recover to Starting.
    assert!(
        wait_for_phase(c, ns, "test-rs-orphan", OdooInstancePhase::Starting).await,
        "expected Starting after orphaned restore job"
    );
}

/// BackingUp → Running when the batch/v1 Job is deleted but the OdooBackupJob
/// CR still exists (zombie CR).  The resolve_job_status() function treats a 404
/// on the K8s Job as a failure, which triggers the normal backup_failed transition.
#[tokio::test]
async fn backup_zombie_crd_recovers_when_k8s_job_deleted() {
    let ctx = TestContext::new("test-bk-zombie").await;
    let (c, ns) = (&ctx.client, ctx.ns.as_str());

    let ready_handle = fast_track_to_running(&ctx, "test-bk-zombie-init").await;

    // Create OdooBackupJob → BackingUp.
    let backup_api: Api<OdooBackupJob> = Api::namespaced(c.clone(), ns);
    let backup_job: OdooBackupJob = serde_json::from_value(json!({
        "apiVersion": "bemade.org/v1alpha1",
        "kind": "OdooBackupJob",
        "metadata": { "name": "test-bk-zombie-job", "namespace": ns },
        "spec": {
            "odooInstanceRef": { "name": "test-bk-zombie" },
            "destination": {
                "bucket": "test-bucket",
                "objectKey": "test-key",
                "endpoint": "http://localhost:9000",
            },
        }
    }))
    .unwrap();
    backup_api
        .create(&PostParams::default(), &backup_job)
        .await
        .expect("failed to create OdooBackupJob");

    assert!(
        wait_for_phase(c, ns, "test-bk-zombie", OdooInstancePhase::BackingUp).await,
        "expected BackingUp after backup job created"
    );

    // Wait for the K8s Job to be created, then delete it (not the CR).
    let k8s_job_name = wait_for_k8s_job_name::<OdooBackupJob>(c, ns, "test-bk-zombie-job").await;
    let jobs_api: Api<Job> = Api::namespaced(c.clone(), ns);
    jobs_api
        .delete(&k8s_job_name, &DeleteParams::default())
        .await
        .expect("failed to delete batch/v1 Job");

    // Instance should recover to Running — resolve_job_status() returns Failed
    // for a 404, triggering the backup_failed transition.
    assert!(
        wait_for_phase(c, ns, "test-bk-zombie", OdooInstancePhase::Running).await,
        "expected Running after K8s Job deleted (zombie CR)"
    );

    ready_handle.abort();
}

/// Initializing → Uninitialized when the OdooInitJob CR is deleted mid-init.
#[tokio::test]
async fn init_job_orphaned_recovers_to_uninitialized() {
    let ctx = TestContext::new("test-in-orphan").await;
    let (c, ns) = (&ctx.client, ctx.ns.as_str());

    assert!(
        wait_for_phase(c, ns, "test-in-orphan", OdooInstancePhase::Uninitialized).await,
        "expected Uninitialized"
    );

    // Create OdooInitJob → Initializing.
    let init_api: Api<OdooInitJob> = Api::namespaced(c.clone(), ns);
    let init_job: OdooInitJob = serde_json::from_value(json!({
        "apiVersion": "bemade.org/v1alpha1",
        "kind": "OdooInitJob",
        "metadata": { "name": "test-in-orphan-job", "namespace": ns },
        "spec": { "odooInstanceRef": { "name": "test-in-orphan" } }
    }))
    .unwrap();
    init_api
        .create(&PostParams::default(), &init_job)
        .await
        .expect("failed to create OdooInitJob");

    assert!(
        wait_for_phase(c, ns, "test-in-orphan", OdooInstancePhase::Initializing).await,
        "expected Initializing after init job created"
    );

    // Delete the OdooInitJob CR.
    init_api
        .delete("test-in-orphan-job", &DeleteParams::default())
        .await
        .expect("failed to delete OdooInitJob");

    // Instance should recover to Uninitialized.
    assert!(
        wait_for_phase(c, ns, "test-in-orphan", OdooInstancePhase::Uninitialized).await,
        "expected Uninitialized after orphaned init job"
    );
}

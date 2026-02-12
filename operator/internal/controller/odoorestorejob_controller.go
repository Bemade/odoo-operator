/*
Copyright 2026 Marc Durepos, Bemade Inc.

This file is part of odoo-operator.

odoo-operator is free software: you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

odoo-operator is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
details.

You should have received a copy of the GNU Lesser General Public License along
with odoo-operator. If not, see <https://www.gnu.org/licenses/>.
*/

package controller

import (
	_ "embed"

	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	bemadev1alpha1 "github.com/bemade/odoo-operator/operator/api/v1alpha1"
)

//go:embed scripts/s3-download.sh
var s3DownloadScript string

//go:embed scripts/odoo-download.sh
var odooDownloadScript string

//go:embed scripts/restore.sh
var restoreScript string

// OdooRestoreJobReconciler reconciles a OdooRestoreJob object.
type OdooRestoreJobReconciler struct {
	client.Client
	Scheme     *runtime.Scheme
	HTTPClient *http.Client
}

// +kubebuilder:rbac:groups=bemade.org,resources=odoorestorejobs,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=bemade.org,resources=odoorestorejobs/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=bemade.org,resources=odoorestorejobs/finalizers,verbs=update
// +kubebuilder:rbac:groups=bemade.org,resources=odooinstances,verbs=get;list;watch
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;delete
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;patch
// +kubebuilder:rbac:groups=core,resources=secrets,verbs=get

func (r *OdooRestoreJobReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	var restoreJob bemadev1alpha1.OdooRestoreJob
	if err := r.Get(ctx, req.NamespacedName, &restoreJob); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	if restoreJob.Status.Phase == bemadev1alpha1.PhaseCompleted ||
		restoreJob.Status.Phase == bemadev1alpha1.PhaseFailed {
		return ctrl.Result{}, nil
	}

	if restoreJob.Status.JobName == "" {
		return r.startJob(ctx, &restoreJob)
	}

	log.Info("checking restore job status", "job", restoreJob.Status.JobName)
	return r.syncJobStatus(ctx, &restoreJob)
}

func (r *OdooRestoreJobReconciler) startJob(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// Guard: if a Job already exists for this CR, adopt it instead of creating a duplicate.
	existing, err := findOwnedJob(ctx, r.Client, restoreJob.UID, restoreJob.Namespace)
	if err != nil {
		return ctrl.Result{}, err
	}
	if existing != nil {
		log.Info("found existing job, adopting", "job", existing.Name)
		patch := client.MergeFrom(restoreJob.DeepCopy())
		restoreJob.Status.Phase = bemadev1alpha1.PhaseRunning
		restoreJob.Status.JobName = existing.Name
		now := metav1.Now()
		restoreJob.Status.StartTime = &now
		return ctrl.Result{}, r.Status().Patch(ctx, restoreJob, patch)
	}

	instanceNS := restoreJob.Spec.OdooInstanceRef.Namespace
	if instanceNS == "" {
		instanceNS = restoreJob.Namespace
	}
	instanceName := restoreJob.Spec.OdooInstanceRef.Name

	var odooInstance bemadev1alpha1.OdooInstance
	if err := r.Get(ctx, types.NamespacedName{Name: instanceName, Namespace: instanceNS}, &odooInstance); err != nil {
		if errors.IsNotFound(err) {
			return r.setFailed(ctx, restoreJob, fmt.Sprintf("OdooInstance %s not found", instanceName))
		}
		return ctrl.Result{}, err
	}

	// Requeue if the instance is not ready for a restore.
	if phase := odooInstance.Status.Phase; phase == bemadev1alpha1.OdooInstancePhaseProvisioning ||
		phase == bemadev1alpha1.OdooInstancePhaseRestoring ||
		phase == bemadev1alpha1.OdooInstancePhaseInitializing {
		log.Info("instance not ready for restore, requeuing", "instance", instanceName, "phase", phase)
		return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
	}

	// Scale down before restoring to prevent writes during restore.
	if err := scaleDeployment(ctx, r.Client, instanceName, instanceNS, 0); err != nil {
		log.Error(err, "failed to scale down deployment — proceeding anyway", "instance", instanceName)
	}

	// Mark the OdooInstance as Restoring.
	if err := patchInstancePhase(ctx, r.Client, instanceName, instanceNS, string(bemadev1alpha1.OdooInstancePhaseRestoring)); err != nil {
		log.Error(err, "failed to set instance phase to Restoring")
	}

	job, err := r.buildRestoreJob(ctx, restoreJob, &odooInstance)
	if err != nil {
		return r.setFailed(ctx, restoreJob, fmt.Sprintf("failed to build job: %v", err))
	}

	if err := r.Create(ctx, job); err != nil {
		return ctrl.Result{}, fmt.Errorf("creating restore job: %w", err)
	}

	log.Info("created restore job", "job", job.Name)

	patch := client.MergeFrom(restoreJob.DeepCopy())
	restoreJob.Status.Phase = bemadev1alpha1.PhaseRunning
	restoreJob.Status.JobName = job.Name
	now := metav1.Now()
	restoreJob.Status.StartTime = &now
	if err := r.Status().Patch(ctx, restoreJob, patch); err != nil {
		return ctrl.Result{}, fmt.Errorf("updating status to Running: %w", err)
	}

	return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
}

func (r *OdooRestoreJobReconciler) syncJobStatus(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	var job batchv1.Job
	if err := r.Get(ctx, types.NamespacedName{Name: restoreJob.Status.JobName, Namespace: restoreJob.Namespace}, &job); err != nil {
		if errors.IsNotFound(err) {
			log.Info("job not found, may have been deleted", "job", restoreJob.Status.JobName)
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	if job.Status.Succeeded > 0 {
		log.Info("restore job succeeded")
		return ctrl.Result{}, r.finalise(ctx, restoreJob, bemadev1alpha1.PhaseCompleted, "")
	}
	if job.Status.Failed > 0 {
		log.Info("restore job failed")
		return ctrl.Result{}, r.finalise(ctx, restoreJob, bemadev1alpha1.PhaseFailed, "restore job failed")
	}

	// Still running — Owns(&batchv1.Job{}) will trigger reconciliation on status change.
	return ctrl.Result{}, nil
}

func (r *OdooRestoreJobReconciler) finalise(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob, phase bemadev1alpha1.Phase, message string) error {
	log := logf.FromContext(ctx)

	patch := client.MergeFrom(restoreJob.DeepCopy())
	restoreJob.Status.Phase = phase
	restoreJob.Status.Message = message
	now := metav1.Now()
	restoreJob.Status.CompletionTime = &now
	if err := r.Status().Patch(ctx, restoreJob, patch); err != nil {
		return fmt.Errorf("updating terminal status: %w", err)
	}

	instanceNS := restoreJob.Spec.OdooInstanceRef.Namespace
	if instanceNS == "" {
		instanceNS = restoreJob.Namespace
	}
	instanceName := restoreJob.Spec.OdooInstanceRef.Name

	if phase == bemadev1alpha1.PhaseCompleted {
		// Restore succeeded: the DB now exists, mark it as initialized and scale up.
		if err := r.markDBInitialized(ctx, instanceName, instanceNS); err != nil {
			log.Error(err, "failed to mark instance as DB-initialized")
		}
		if err := r.scaleInstanceBackUp(ctx, instanceName, instanceNS); err != nil {
			log.Error(err, "failed to scale instance back up")
		}
	} else {
		// Restore failed: DB may be in a broken state, leave the instance stopped.
		log.Info("restore failed, leaving instance stopped", "instance", instanceName)
	}

	if restoreJob.Spec.Webhook != nil {
		r.notifyWebhook(ctx, restoreJob, phase)
	}

	return nil
}

// markDBInitialized sets status.dbInitialized=true on the OdooInstance.
func (r *OdooRestoreJobReconciler) markDBInitialized(ctx context.Context, instanceName, instanceNS string) error {
	var instance bemadev1alpha1.OdooInstance
	if err := r.Get(ctx, types.NamespacedName{Name: instanceName, Namespace: instanceNS}, &instance); err != nil {
		return err
	}
	if instance.Status.DBInitialized {
		return nil
	}
	patch := client.MergeFrom(instance.DeepCopy())
	instance.Status.DBInitialized = true
	return r.Status().Patch(ctx, &instance, patch)
}

func (r *OdooRestoreJobReconciler) scaleInstanceBackUp(ctx context.Context, instanceName, instanceNS string) error {
	var odooInstance bemadev1alpha1.OdooInstance
	if err := r.Get(ctx, types.NamespacedName{Name: instanceName, Namespace: instanceNS}, &odooInstance); err != nil {
		return err
	}
	replicas := odooInstance.Spec.Replicas
	if replicas == 0 {
		replicas = 1
	}
	return scaleDeployment(ctx, r.Client, instanceName, instanceNS, replicas)
}

func (r *OdooRestoreJobReconciler) setFailed(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob, message string) (ctrl.Result, error) {
	patch := client.MergeFrom(restoreJob.DeepCopy())
	restoreJob.Status.Phase = bemadev1alpha1.PhaseFailed
	restoreJob.Status.Message = message
	return ctrl.Result{}, r.Status().Patch(ctx, restoreJob, patch)
}

func (r *OdooRestoreJobReconciler) buildRestoreJob(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob, odooInstance *bemadev1alpha1.OdooInstance) (*batchv1.Job, error) {
	instanceName := odooInstance.Name
	instanceUID := string(odooInstance.UID)

	image := odooInstance.Spec.Image
	if image == "" {
		image = "odoo:18.0"
	}

	var imagePullSecrets []corev1.LocalObjectReference
	if odooInstance.Spec.ImagePullSecret != "" {
		imagePullSecrets = []corev1.LocalObjectReference{{Name: odooInstance.Spec.ImagePullSecret}}
	}

	odooConfName := fmt.Sprintf("%s-odoo-conf", instanceName)
	dbName := fmt.Sprintf("odoo_%s", sanitiseUID(instanceUID))

	format := string(restoreJob.Spec.Format)
	if format == "" {
		format = "zip"
	}

	// neutralize matches the Python convention: "True" / "False" (capitalised).
	neutralize := "True"
	if !restoreJob.Spec.Neutralize {
		neutralize = "False"
	}

	src := restoreJob.Spec.Source

	// Output file written by the download init container; detected by name in restoreScript.
	outputFile := "/mnt/backup/backup.zip"
	switch bemadev1alpha1.BackupFormat(format) {
	case bemadev1alpha1.BackupFormatDump:
		outputFile = "/mnt/backup/dump.dump"
	case bemadev1alpha1.BackupFormatSQL:
		outputFile = "/mnt/backup/dump.sql"
	}

	sharedMount := corev1.VolumeMount{Name: "backup", MountPath: "/mnt/backup"}

	// Restore container env — detects format by filename, no need to pass it.
	dbEnv := []corev1.EnvVar{
		{Name: "DB_NAME", Value: dbName},
		{Name: "NEUTRALIZE", Value: neutralize},
		{
			Name: "HOST",
			ValueFrom: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: odooConfName},
					Key:                  "db_host",
				},
			},
		},
		{
			Name: "PORT",
			ValueFrom: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: odooConfName},
					Key:                  "db_port",
				},
			},
		},
		{
			Name: "USER",
			ValueFrom: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: odooConfName},
					Key:                  "db_user",
				},
			},
		},
		{
			Name: "PASSWORD",
			ValueFrom: &corev1.EnvVarSource{
				ConfigMapKeyRef: &corev1.ConfigMapKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: odooConfName},
					Key:                  "db_password",
				},
			},
		},
	}

	// Build the optional download init container based on source type.
	var initContainers []corev1.Container

	switch src.Type {
	case bemadev1alpha1.RestoreSourceTypeS3:
		if src.S3 != nil {
			insecureVal := "false"
			if src.S3.Insecure {
				insecureVal = "true"
			}
			dlEnv := []corev1.EnvVar{
				{Name: "S3_BUCKET", Value: src.S3.Bucket},
				{Name: "S3_KEY", Value: src.S3.ObjectKey},
				{Name: "S3_ENDPOINT", Value: src.S3.Endpoint},
				{Name: "S3_INSECURE", Value: insecureVal},
				{Name: "OUTPUT_FILE", Value: outputFile},
				{Name: "MC_CONFIG_DIR", Value: "/tmp/.mc"},
			}
			if src.S3.CredentialsSecretRef != nil {
				credNS := src.S3.CredentialsSecretRef.Namespace
				if credNS == "" {
					credNS = restoreJob.Namespace
				}
				accessKey, secretKey, err := readS3Credentials(ctx, r.Client, src.S3.CredentialsSecretRef.Name, credNS)
				if err != nil {
					return nil, fmt.Errorf("reading S3 credentials: %w", err)
				}
				dlEnv = append(dlEnv,
					corev1.EnvVar{Name: "AWS_ACCESS_KEY_ID", Value: accessKey},
					corev1.EnvVar{Name: "AWS_SECRET_ACCESS_KEY", Value: secretKey},
				)
			}
			initContainers = append(initContainers, corev1.Container{
				Name:         "download",
				Image:        "quay.io/minio/mc:latest",
				Command:      []string{"/bin/sh", "-c", s3DownloadScript},
				Env:          dlEnv,
				VolumeMounts: []corev1.VolumeMount{sharedMount},
			})
		}
	case bemadev1alpha1.RestoreSourceTypeOdoo:
		if src.Odoo == nil {
			return nil, fmt.Errorf("source.odoo must be set when source.type is %q", bemadev1alpha1.RestoreSourceTypeOdoo)
		}
		if src.Odoo.SourceDatabase == "" {
			return nil, fmt.Errorf("source.odoo.sourceDatabase is required for Odoo source restores")
		}
		backupFormat := "zip"
		if bemadev1alpha1.BackupFormat(format) != bemadev1alpha1.BackupFormatZip {
			backupFormat = "dump"
		}
		dlEnv := []corev1.EnvVar{
			{Name: "ODOO_URL", Value: src.Odoo.URL},
			{Name: "SOURCE_DB", Value: src.Odoo.SourceDatabase},
			{Name: "MASTER_PASSWORD", Value: src.Odoo.MasterPassword},
			{Name: "BACKUP_FORMAT", Value: backupFormat},
			{Name: "OUTPUT_FILE", Value: outputFile},
		}
		initContainers = append(initContainers, corev1.Container{
			Name:         "download",
			Image:        "curlimages/curl:latest",
			Command:      []string{"/bin/sh", "-c", odooDownloadScript},
			Env:          dlEnv,
			VolumeMounts: []corev1.VolumeMount{sharedMount},
		})
	}

	ttl := int32(900)
	backoffLimit := int32(0)
	activeDeadline := int64(3600) // 1 hour

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: fmt.Sprintf("%s-", restoreJob.Name),
			Namespace:    restoreJob.Namespace,
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoffLimit,
			TTLSecondsAfterFinished: &ttl,
			ActiveDeadlineSeconds:   &activeDeadline,
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy:    corev1.RestartPolicyNever,
					ImagePullSecrets: imagePullSecrets,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsUser:           ptr(int64(100)),
						RunAsGroup:          ptr(int64(101)),
						FSGroup:             ptr(int64(101)),
						FSGroupChangePolicy: ptr(corev1.FSGroupChangeOnRootMismatch),
					},
					Volumes: []corev1.Volume{
						{
							Name: "filestore",
							VolumeSource: corev1.VolumeSource{
								PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
									ClaimName: fmt.Sprintf("%s-filestore-pvc", instanceName),
								},
							},
						},
						{
							Name: "odoo-conf",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: fmt.Sprintf("%s-odoo-conf", instanceName),
									},
								},
							},
						},
						{
							Name:         "backup",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
						},
					},
					InitContainers: initContainers,
					Containers: []corev1.Container{
						{
							Name:    "restore",
							Image:   image,
							Command: []string{"/bin/sh", "-c", restoreScript},
							Env:     dbEnv,
							VolumeMounts: []corev1.VolumeMount{
								{Name: "filestore", MountPath: "/var/lib/odoo"},
								{Name: "odoo-conf", MountPath: "/etc/odoo"},
								sharedMount,
							},
						},
					},
				},
			},
		},
	}

	if err := controllerutil.SetControllerReference(restoreJob, job, r.Scheme); err != nil {
		return nil, err
	}
	return job, nil
}

func (r *OdooRestoreJobReconciler) notifyWebhook(ctx context.Context, restoreJob *bemadev1alpha1.OdooRestoreJob, phase bemadev1alpha1.Phase) {
	log := logf.FromContext(ctx)
	wh := restoreJob.Spec.Webhook

	token := wh.Token
	if token == "" && wh.SecretTokenSecretRef != nil {
		var secret corev1.Secret
		if err := r.Get(ctx, types.NamespacedName{
			Name:      wh.SecretTokenSecretRef.Name,
			Namespace: restoreJob.Namespace,
		}, &secret); err == nil {
			token = string(secret.Data[wh.SecretTokenSecretRef.Key])
		}
	}

	data := map[string]any{
		"phase":   phase,
		"jobName": restoreJob.Status.JobName,
	}
	if restoreJob.Status.Message != "" {
		data["message"] = restoreJob.Status.Message
	}
	if restoreJob.Status.CompletionTime != nil {
		data["completionTime"] = restoreJob.Status.CompletionTime.UTC().Format("2006-01-02 15:04:05")
	}
	payload, _ := json.Marshal(data)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, wh.URL, bytes.NewReader(payload))
	if err != nil {
		log.Error(err, "failed to build webhook request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	httpClient := r.HTTPClient
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		log.Error(err, "webhook notification failed")
		return
	}
	defer resp.Body.Close()
	log.Info("webhook notification sent", "status", resp.StatusCode)
}

func (r *OdooRestoreJobReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&bemadev1alpha1.OdooRestoreJob{}).
		Owns(&batchv1.Job{}).
		Named("odoorestorejob").
		Complete(r)
}

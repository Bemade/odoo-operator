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
	"path/filepath"
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

//go:embed scripts/backup.sh
var backupScript string

//go:embed scripts/s3-upload.sh
var uploadScript string

// OdooBackupJobReconciler reconciles a OdooBackupJob object.
type OdooBackupJobReconciler struct {
	client.Client
	Scheme     *runtime.Scheme
	HTTPClient *http.Client
}

// +kubebuilder:rbac:groups=bemade.org,resources=odoobackupjobs,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=bemade.org,resources=odoobackupjobs/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=bemade.org,resources=odoobackupjobs/finalizers,verbs=update
// +kubebuilder:rbac:groups=bemade.org,resources=odooinstances,verbs=get;list;watch
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;delete
// +kubebuilder:rbac:groups=core,resources=secrets,verbs=get

func (r *OdooBackupJobReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	var backupJob bemadev1alpha1.OdooBackupJob
	if err := r.Get(ctx, req.NamespacedName, &backupJob); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	if backupJob.Status.Phase == bemadev1alpha1.PhaseCompleted ||
		backupJob.Status.Phase == bemadev1alpha1.PhaseFailed {
		return ctrl.Result{}, nil
	}

	if backupJob.Status.JobName == "" {
		return r.startJob(ctx, &backupJob)
	}

	log.Info("checking backup job status", "job", backupJob.Status.JobName)
	return r.syncJobStatus(ctx, &backupJob)
}

func (r *OdooBackupJobReconciler) startJob(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// Guard: if a Job already exists for this CR, adopt it instead of creating a duplicate.
	existing, err := findOwnedJob(ctx, r.Client, backupJob.UID, backupJob.Namespace)
	if err != nil {
		return ctrl.Result{}, err
	}
	if existing != nil {
		log.Info("found existing job, adopting", "job", existing.Name)
		patch := client.MergeFrom(backupJob.DeepCopy())
		backupJob.Status.Phase = bemadev1alpha1.PhaseRunning
		backupJob.Status.JobName = existing.Name
		now := metav1.Now()
		backupJob.Status.StartTime = &now
		return ctrl.Result{}, r.Status().Patch(ctx, backupJob, patch)
	}

	instanceNS := backupJob.Spec.OdooInstanceRef.Namespace
	if instanceNS == "" {
		instanceNS = backupJob.Namespace
	}
	instanceName := backupJob.Spec.OdooInstanceRef.Name

	var odooInstance bemadev1alpha1.OdooInstance
	if err := r.Get(ctx, types.NamespacedName{Name: instanceName, Namespace: instanceNS}, &odooInstance); err != nil {
		if errors.IsNotFound(err) {
			return r.setFailed(ctx, backupJob, fmt.Sprintf("OdooInstance %s not found", instanceName))
		}
		return ctrl.Result{}, err
	}

	// Backup runs alongside the live instance — no scale-down needed.

	job, err := r.buildBackupJob(ctx, backupJob, &odooInstance)
	if err != nil {
		return r.setFailed(ctx, backupJob, fmt.Sprintf("failed to build job: %v", err))
	}

	if err := r.Create(ctx, job); err != nil {
		return ctrl.Result{}, fmt.Errorf("creating backup job: %w", err)
	}

	log.Info("created backup job", "job", job.Name)

	patch := client.MergeFrom(backupJob.DeepCopy())
	backupJob.Status.Phase = bemadev1alpha1.PhaseRunning
	backupJob.Status.JobName = job.Name
	now := metav1.Now()
	backupJob.Status.StartTime = &now
	if err := r.Status().Patch(ctx, backupJob, patch); err != nil {
		return ctrl.Result{}, fmt.Errorf("updating status to Running: %w", err)
	}

	return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
}

func (r *OdooBackupJobReconciler) syncJobStatus(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	var job batchv1.Job
	if err := r.Get(ctx, types.NamespacedName{Name: backupJob.Status.JobName, Namespace: backupJob.Namespace}, &job); err != nil {
		if errors.IsNotFound(err) {
			log.Info("job not found, may have been deleted", "job", backupJob.Status.JobName)
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	if job.Status.Succeeded > 0 {
		log.Info("backup job succeeded")
		return ctrl.Result{}, r.finalise(ctx, backupJob, bemadev1alpha1.PhaseCompleted, "")
	}
	if job.Status.Failed > 0 {
		log.Info("backup job failed")
		return ctrl.Result{}, r.finalise(ctx, backupJob, bemadev1alpha1.PhaseFailed, "backup job failed")
	}

	// Still running — Owns(&batchv1.Job{}) will trigger reconciliation on status change.
	return ctrl.Result{}, nil
}

func (r *OdooBackupJobReconciler) finalise(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob, phase bemadev1alpha1.Phase, message string) error {
	patch := client.MergeFrom(backupJob.DeepCopy())
	backupJob.Status.Phase = phase
	backupJob.Status.Message = message
	now := metav1.Now()
	backupJob.Status.CompletionTime = &now
	if err := r.Status().Patch(ctx, backupJob, patch); err != nil {
		return fmt.Errorf("updating terminal status: %w", err)
	}

	if backupJob.Spec.Webhook != nil {
		r.notifyWebhook(ctx, backupJob, phase)
	}

	return nil
}

func (r *OdooBackupJobReconciler) setFailed(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob, message string) (ctrl.Result, error) {
	patch := client.MergeFrom(backupJob.DeepCopy())
	backupJob.Status.Phase = bemadev1alpha1.PhaseFailed
	backupJob.Status.Message = message
	return ctrl.Result{}, r.Status().Patch(ctx, backupJob, patch)
}

func (r *OdooBackupJobReconciler) buildBackupJob(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob, odooInstance *bemadev1alpha1.OdooInstance) (*batchv1.Job, error) {
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

	dest := backupJob.Spec.Destination
	objectKey := dest.ObjectKey
	localFilename := ""
	if objectKey != "" {
		localFilename = filepath.Base(objectKey)
	} else {
		localFilename = fmt.Sprintf("%s-backup", instanceName)
	}

	format := string(backupJob.Spec.Format)
	if format == "" {
		format = "zip"
	}

	withFilestore := "true"
	if !backupJob.Spec.WithFilestore {
		withFilestore = "false"
	}

	// Backup container — runs as init container so the uploader waits for it.
	backupEnv := []corev1.EnvVar{
		{Name: "INSTANCE_NAME", Value: instanceName},
		{Name: "DB_NAME", Value: dbName},
		{Name: "BACKUP_FORMAT", Value: format},
		{Name: "BACKUP_WITH_FILESTORE", Value: withFilestore},
		{Name: "LOCAL_FILENAME", Value: localFilename},
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

	insecureVal := "false"
	if dest.Insecure {
		insecureVal = "true"
	}

	// Uploader container — runs after the backup init container completes.
	uploadEnv := []corev1.EnvVar{
		{Name: "S3_BUCKET", Value: dest.Bucket},
		{Name: "S3_KEY", Value: objectKey},
		{Name: "S3_ENDPOINT", Value: dest.Endpoint},
		{Name: "S3_INSECURE", Value: insecureVal},
		{Name: "MC_CONFIG_DIR", Value: "/tmp/.mc"},
	}
	if dest.S3CredentialsSecretRef != nil {
		credNS := dest.S3CredentialsSecretRef.Namespace
		if credNS == "" {
			credNS = backupJob.Namespace
		}
		accessKey, secretKey, err := readS3Credentials(ctx, r.Client, dest.S3CredentialsSecretRef.Name, credNS)
		if err != nil {
			return nil, fmt.Errorf("reading S3 credentials: %w", err)
		}
		uploadEnv = append(uploadEnv,
			corev1.EnvVar{Name: "AWS_ACCESS_KEY_ID", Value: accessKey},
			corev1.EnvVar{Name: "AWS_SECRET_ACCESS_KEY", Value: secretKey},
		)
	}

	sharedMount := corev1.VolumeMount{Name: "backup", MountPath: "/mnt/backup"}

	ttl := int32(900)
	backoffLimit := int32(0)
	activeDeadline := int64(1800) // 30 min

	// Pod affinity: schedule on the same node as the Odoo Deployment so that
	// RWO filestore PVCs can be mounted.
	affinity := &corev1.Affinity{
		PodAffinity: &corev1.PodAffinity{
			RequiredDuringSchedulingIgnoredDuringExecution: []corev1.PodAffinityTerm{
				{
					LabelSelector: &metav1.LabelSelector{
						MatchLabels: map[string]string{"app": instanceName},
					},
					TopologyKey: "kubernetes.io/hostname",
				},
			},
		},
	}

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: fmt.Sprintf("%s-", backupJob.Name),
			Namespace:    backupJob.Namespace,
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoffLimit,
			TTLSecondsAfterFinished: &ttl,
			ActiveDeadlineSeconds:   &activeDeadline,
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy:    corev1.RestartPolicyNever,
					ImagePullSecrets: imagePullSecrets,
					Affinity:         affinity,
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
					// Init container creates the backup artifact.
					InitContainers: []corev1.Container{
						{
							Name:    "backup",
							Image:   image,
							Command: []string{"/bin/sh", "-c", backupScript},
							Env:     backupEnv,
							VolumeMounts: []corev1.VolumeMount{
								{Name: "filestore", MountPath: "/var/lib/odoo"},
								{Name: "odoo-conf", MountPath: "/etc/odoo"},
								sharedMount,
							},
						},
					},
					// Main container uploads the artifact to S3.
					Containers: []corev1.Container{
						{
							Name:         "uploader",
							Image:        "quay.io/minio/mc:latest",
							Command:      []string{"/bin/sh", "-c", uploadScript},
							Env:          uploadEnv,
							VolumeMounts: []corev1.VolumeMount{sharedMount},
						},
					},
				},
			},
		},
	}

	if err := controllerutil.SetControllerReference(backupJob, job, r.Scheme); err != nil {
		return nil, err
	}
	return job, nil
}

func (r *OdooBackupJobReconciler) notifyWebhook(ctx context.Context, backupJob *bemadev1alpha1.OdooBackupJob, phase bemadev1alpha1.Phase) {
	log := logf.FromContext(ctx)
	wh := backupJob.Spec.Webhook

	token := wh.Token
	if token == "" && wh.SecretTokenSecretRef != nil {
		var secret corev1.Secret
		if err := r.Get(ctx, types.NamespacedName{
			Name:      wh.SecretTokenSecretRef.Name,
			Namespace: backupJob.Namespace,
		}, &secret); err == nil {
			token = string(secret.Data[wh.SecretTokenSecretRef.Key])
		}
	}

	data := map[string]any{
		"phase":   phase,
		"jobName": backupJob.Status.JobName,
	}
	if backupJob.Status.Message != "" {
		data["message"] = backupJob.Status.Message
	}
	if backupJob.Status.CompletionTime != nil {
		data["completionTime"] = backupJob.Status.CompletionTime.UTC().Format("2006-01-02 15:04:05")
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

func (r *OdooBackupJobReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&bemadev1alpha1.OdooBackupJob{}).
		Owns(&batchv1.Job{}).
		Named("odoobackupjob").
		Complete(r)
}

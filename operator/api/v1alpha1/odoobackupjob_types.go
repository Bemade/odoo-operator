/*
Copyright 2026 Bemade Inc..

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package v1alpha1

import metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

// BackupDestination specifies where to store the backup artifact.
type BackupDestination struct {
	// s3 configures upload to an S3-compatible object store.
	S3 S3Config `json:"s3"`
}

// OdooBackupJobSpec defines the desired state of OdooBackupJob.
type OdooBackupJobSpec struct {
	// odooInstanceRef identifies the OdooInstance to back up.
	// +kubebuilder:validation:Required
	OdooInstanceRef OdooInstanceRef `json:"odooInstanceRef"`

	// destination specifies where the backup artifact is uploaded.
	// +kubebuilder:validation:Required
	Destination BackupDestination `json:"destination"`

	// format is the backup format.
	// +optional
	// +kubebuilder:default=zip
	Format BackupFormat `json:"format,omitempty"`

	// withFilestore includes the Odoo filestore in the backup when format is zip.
	// +optional
	// +kubebuilder:default=true
	WithFilestore bool `json:"withFilestore,omitempty"`

	// webhook is an optional callback invoked when the job completes or fails.
	// +optional
	Webhook *WebhookConfig `json:"webhook,omitempty"`
}

// OdooBackupJobStatus defines the observed state of OdooBackupJob.
type OdooBackupJobStatus struct {
	// phase is the current lifecycle phase of the backup job.
	// +optional
	Phase Phase `json:"phase,omitempty"`

	// jobName is the name of the Kubernetes Job performing the backup.
	// +optional
	JobName string `json:"jobName,omitempty"`

	// startTime is when the backup job began executing.
	// +optional
	StartTime *metav1.Time `json:"startTime,omitempty"`

	// completionTime is when the backup job finished.
	// +optional
	CompletionTime *metav1.Time `json:"completionTime,omitempty"`

	// message is a human-readable description of the current status.
	// +optional
	Message string `json:"message,omitempty"`

	// conditions represent the detailed state of this resource.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=backupjob
// +kubebuilder:printcolumn:name="Target",type=string,JSONPath=`.spec.odooInstanceRef.name`
// +kubebuilder:printcolumn:name="Format",type=string,JSONPath=`.spec.format`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// OdooBackupJob runs a one-shot backup of an OdooInstance to S3.
type OdooBackupJob struct {
	metav1.TypeMeta `json:",inline"`

	// +optional
	metav1.ObjectMeta `json:"metadata,omitzero"`

	// +required
	Spec OdooBackupJobSpec `json:"spec"`

	// +optional
	Status OdooBackupJobStatus `json:"status,omitzero"`
}

// +kubebuilder:object:root=true

// OdooBackupJobList contains a list of OdooBackupJob.
type OdooBackupJobList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []OdooBackupJob `json:"items"`
}

func init() {
	SchemeBuilder.Register(&OdooBackupJob{}, &OdooBackupJobList{})
}

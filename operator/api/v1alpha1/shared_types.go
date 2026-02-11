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

import corev1 "k8s.io/api/core/v1"

// BackupFormat specifies the format of the backup artifact.
// +kubebuilder:validation:Enum=zip;sql;dump
type BackupFormat string

const (
	// BackupFormatZip creates an Odoo-format zip archive including the filestore.
	BackupFormatZip BackupFormat = "zip"
	// BackupFormatSQL creates a plain-text SQL dump via pg_dump.
	BackupFormatSQL BackupFormat = "sql"
	// BackupFormatDump creates a PostgreSQL custom-format dump via pg_dump.
	BackupFormatDump BackupFormat = "dump"
)

// S3Config holds connection details for an S3-compatible object store.
type S3Config struct {
	// bucket is the S3 bucket name.
	Bucket string `json:"bucket"`

	// objectKey is the object key (path) within the bucket.
	ObjectKey string `json:"objectKey"`

	// endpoint is the S3-compatible endpoint URL (e.g. "https://s3.example.com").
	Endpoint string `json:"endpoint"`

	// region is the optional S3 region.
	// +optional
	Region string `json:"region,omitempty"`

	// insecure disables TLS certificate verification.
	// +optional
	// +kubebuilder:default=false
	Insecure bool `json:"insecure,omitempty"`

	// credentialsSecretRef references a Secret with accessKey and secretKey fields.
	// +optional
	CredentialsSecretRef *corev1.SecretReference `json:"credentialsSecretRef,omitempty"`
}

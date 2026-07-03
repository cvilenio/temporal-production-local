// Package v1alpha1 contains the WorkerAutoscaler API, the CRD this controller
// reconciles. One WorkerAutoscaler declares how a single Temporal worker
// (a WorkerDeployment, i.e. all of its build-id versions) should be scaled from
// live task-queue backlog. See ADR-0023.
//
// +kubebuilder:object:generate=true
// +groupName=autoscaling.ziggymart.io
package v1alpha1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

// GroupVersion is the group/version for this API.
var GroupVersion = schema.GroupVersion{Group: "autoscaling.ziggymart.io", Version: "v1alpha1"}

// SchemeBuilder registers the API types with a runtime.Scheme.
var SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

// AddToScheme adds the types in this group-version to the given scheme.
var AddToScheme = SchemeBuilder.AddToScheme

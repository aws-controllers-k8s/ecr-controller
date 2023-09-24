package repository

import (
	"context"
	"strconv"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackerr "github.com/aws-controllers-k8s/runtime/pkg/errors"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	ackutil "github.com/aws-controllers-k8s/runtime/pkg/util"
	svcsdk "github.com/aws/aws-sdk-go/service/ecr"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	svcapitypes "github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
)

const (
	// AnnotationPrefix is the prefix for all annotations specifically for
	// the ECR service.
	AnnotationPrefix = "ecr.services.k8s.aws/"
	// AnnotationDeleteForce is an annotation whose value indicates whether
	// the repository should be removed if it contains images.
	AnnotationDeleteForce = AnnotationPrefix + "force-delete"

	DefaultDeleteForce = false
)

// GetDeleteForce returns whether the repository should be deleted if it
// contains images as determined by the annotation on the object, or the
// default value otherwise.
func GetDeleteForce(
	m *metav1.ObjectMeta,
) bool {
	resAnnotations := m.GetAnnotations()
	deleteForce, ok := resAnnotations[AnnotationDeleteForce]
	if !ok {
		return DefaultDeleteForce
	}

	deleteForceBool, err := strconv.ParseBool(deleteForce)
	if err != nil {
		return DefaultDeleteForce
	}

	return deleteForceBool
}

// setResourceAdditionalFields will describe the fields that are not return by
// DescribeRepository calls
func (rm *resourceManager) setResourceAdditionalFields(
	ctx context.Context,
	ko *svcapitypes.Repository,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.setResourceAdditionalFields")
	defer exit(err)

	// Set repository policy
	ko.Spec.Policy, err = rm.getRepositoryPolicy(ctx, *ko.Spec.Name, *ko.Spec.RegistryID)
	if err != nil {
		return err
	}
	// Set repository lifecycle policy
	ko.Spec.LifecyclePolicy, err = rm.getRepositoryLifecyclePolicy(ctx, *ko.Spec.Name, *ko.Spec.RegistryID)
	if err != nil {
		return err
	}
	// Set repository tags
	ko.Spec.Tags, err = rm.getRepositoryTags(ctx, string(*ko.Status.ACKResourceMetadata.ARN))
	if err != nil {
		return err
	}

	return nil
}

// getRepositoryPolicy retrieves a repository permissions policy.
func (rm *resourceManager) getRepositoryPolicy(
	ctx context.Context,
	repositoryName,
	registryID string,
) (*string, error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.getRepositoryPolicy")
	var err error
	defer exit(err)

	var getRepositoryPolicyResponse *svcsdk.GetRepositoryPolicyOutput
	getRepositoryPolicyResponse, err = rm.sdkapi.GetRepositoryPolicyWithContext(
		ctx,
		&svcsdk.GetRepositoryPolicyInput{
			RepositoryName: &repositoryName,
			RegistryId:     &registryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetRepositoryPolicy", err)
	if err != nil {
		if awsErr, ok := ackerr.AWSError(err); !ok || awsErr.Code() != svcsdk.ErrCodeRepositoryPolicyNotFoundException {
			return nil, err
		}
		// do not return an error if the repository policy is not found. Simply return an empty policy.
		return nil, nil
	}
	return getRepositoryPolicyResponse.PolicyText, nil
}

// getRepositoryLifecyclePolicy retrieves a repository lifecycle policy.
func (rm *resourceManager) getRepositoryLifecyclePolicy(
	ctx context.Context,
	repositoryName,
	registryID string,
) (*string, error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.getRepositoryLifecyclePolicy")
	var err error
	defer exit(err)

	var getLifecyclePolicyResponse *svcsdk.GetLifecyclePolicyOutput
	getLifecyclePolicyResponse, err = rm.sdkapi.GetLifecyclePolicyWithContext(
		ctx,
		&svcsdk.GetLifecyclePolicyInput{
			RepositoryName: &repositoryName,
			RegistryId:     &registryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetLifecyclePolicy", err)
	if err != nil {
		if awsErr, ok := ackerr.AWSError(err); !ok || awsErr.Code() != svcsdk.ErrCodeLifecyclePolicyNotFoundException {
			return nil, err
		}
		// do not return an error if the lifecycle policy is not found. Simply return an empty lifecycle policy.
		return nil, nil
	}
	return getLifecyclePolicyResponse.LifecyclePolicyText, nil
}

// getRepositoryTags retrieves a resource list of tags.
func (rm *resourceManager) getRepositoryTags(ctx context.Context, resourceARN string) ([]*svcapitypes.Tag, error) {
	listTagsForResourceResponse, err := rm.sdkapi.ListTagsForResourceWithContext(
		ctx,
		&svcsdk.ListTagsForResourceInput{
			ResourceArn: &resourceARN,
		},
	)
	rm.metrics.RecordAPICall("GET", "ListTagsForResource", err)
	if err != nil {
		return nil, err
	}
	tags := make([]*svcapitypes.Tag, 0, len(listTagsForResourceResponse.Tags))
	for _, tag := range listTagsForResourceResponse.Tags {
		tags = append(tags, &svcapitypes.Tag{
			Key:   tag.Key,
			Value: tag.Value,
		})
	}
	return tags, nil
}

func customPreCompare(
	delta *ackcompare.Delta,
	a *resource,
	b *resource,
) {
	if len(a.ko.Spec.Tags) != len(b.ko.Spec.Tags) {
		delta.Add("Spec.Tags", a.ko.Spec.Tags, b.ko.Spec.Tags)
	} else if len(a.ko.Spec.Tags) > 0 {
		if !equalTags(a.ko.Spec.Tags, b.ko.Spec.Tags) {
			delta.Add("Spec.Tags", a.ko.Spec.Tags, b.ko.Spec.Tags)
		}
	}
}

// equalTags returns true if two Tag arrays are equal regardless of the order
// of their elements.
func equalTags(
	a []*svcapitypes.Tag,
	b []*svcapitypes.Tag,
) bool {
	added, updated, removed := computeTagsDelta(a, b)
	return len(added) == 0 && len(updated) == 0 && len(removed) == 0
}

// computeTagsDelta compares two Tag arrays and return three different list
// containing the added, updated and removed tags.
// The removed tags only contains the Key of tags
func computeTagsDelta(
	a []*svcapitypes.Tag,
	b []*svcapitypes.Tag,
) (added, updated []*svcapitypes.Tag, removed []*string) {
	var visitedIndexes []string
mainLoop:
	for _, aElement := range a {
		visitedIndexes = append(visitedIndexes, *aElement.Key)
		for _, bElement := range b {
			if equalStrings(aElement.Key, bElement.Key) {
				if !equalStrings(aElement.Value, bElement.Value) {
					updated = append(updated, bElement)
				}
				continue mainLoop
			}
		}
		removed = append(removed, aElement.Key)
	}
	for _, bElement := range b {
		if !ackutil.InStrings(*bElement.Key, visitedIndexes) {
			added = append(added, bElement)
		}
	}
	return added, updated, removed
}

// svcTagsFromResourceTags transforms a *svcapitypes.Tag array to a *svcsdk.Tag array.
func sdkTagsFromResourceTags(rTags []*svcapitypes.Tag) []*svcsdk.Tag {
	tags := make([]*svcsdk.Tag, len(rTags))
	for i := range rTags {
		tags[i] = &svcsdk.Tag{
			Key:   rTags[i].Key,
			Value: rTags[i].Value,
		}
	}
	return tags
}

func equalStrings(a, b *string) bool {
	if a == nil {
		return b == nil || *b == ""
	}
	return (*a == "" && b == nil) || *a == *b
}

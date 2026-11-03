package repository

import (
	"context"
	"errors"
	"strconv"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/ecr"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/ecr/types"
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
	getRepositoryPolicyResponse, err = rm.sdkapi.GetRepositoryPolicy(
		ctx,
		&svcsdk.GetRepositoryPolicyInput{
			RepositoryName: &repositoryName,
			RegistryId:     &registryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetRepositoryPolicy", err)
	if err != nil {
		var notFound *svcsdktypes.RepositoryPolicyNotFoundException
		if !errors.As(err, &notFound) {
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
	getLifecyclePolicyResponse, err = rm.sdkapi.GetLifecyclePolicy(
		ctx,
		&svcsdk.GetLifecyclePolicyInput{
			RepositoryName: &repositoryName,
			RegistryId:     &registryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetLifecyclePolicy", err)
	if err != nil {
		var notFound *svcsdktypes.LifecyclePolicyNotFoundException
		if !errors.As(err, &notFound) {
			return nil, err
		}
		// do not return an error if the lifecycle policy is not found. Simply return an empty lifecycle policy.
		return nil, nil
	}
	return getLifecyclePolicyResponse.LifecyclePolicyText, nil
}

// getRepositoryTags retrieves a resource list of tags.
func (rm *resourceManager) getRepositoryTags(ctx context.Context, resourceARN string) ([]*svcapitypes.Tag, error) {
	listTagsForResourceResponse, err := rm.sdkapi.ListTagsForResource(
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
	aTags, _ := convertToOrderedACKTags(a.ko.Spec.Tags)
	bTags, _ := convertToOrderedACKTags(b.ko.Spec.Tags)
	// from=latest (b), to=desired (a); GetTagsDifference upsert semantics: value-changed tags appear in addedTags only.
	addedTags, _, removedTags := ackcompare.GetTagsDifference(bTags, aTags)
	if len(addedTags) > 0 || len(removedTags) > 0 {
		delta.Add("Spec.Tags", a.ko.Spec.Tags, b.ko.Spec.Tags)
	}
}

// svcTagsFromResourceTags transforms a *svcapitypes.Tag array to a *svcsdk.Tag array.
func sdkTagsFromResourceTags(rTags []*svcapitypes.Tag) []svcsdktypes.Tag {
	tags := make([]svcsdktypes.Tag, len(rTags))
	for i := range rTags {
		tags[i] = svcsdktypes.Tag{
			Key:   rTags[i].Key,
			Value: rTags[i].Value,
		}
	}
	return tags
}

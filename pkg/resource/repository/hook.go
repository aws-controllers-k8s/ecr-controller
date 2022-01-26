package repository

import (
	"context"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackerr "github.com/aws-controllers-k8s/runtime/pkg/errors"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	ackutil "github.com/aws-controllers-k8s/runtime/pkg/util"
	svcsdk "github.com/aws/aws-sdk-go/service/ecr"

	"github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
	svcapitypes "github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
)

// setResourceAdditionalFields will describe the fields that are not return by
// DescribeRepository calls
func (rm *resourceManager) setResourceAdditionalFields(
	ctx context.Context,
	ko *svcapitypes.Repository,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.setResourceAdditionalFields")
	defer exit(err)

	var getLifecyclePolicyResponse *svcsdk.GetLifecyclePolicyOutput
	getLifecyclePolicyResponse, err = rm.sdkapi.GetLifecyclePolicyWithContext(
		ctx,
		&svcsdk.GetLifecyclePolicyInput{
			RepositoryName: ko.Spec.Name,
			RegistryId:     ko.Spec.RegistryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetLifecyclePolicy", err)
	if err != nil {
		if awsErr, ok := ackerr.AWSError(err); !ok || awsErr.Code() != svcsdk.ErrCodeLifecyclePolicyNotFoundException {
			return err
		}
		ko.Spec.LifecyclePolicy = nil
	}
	if getLifecyclePolicyResponse.LifecyclePolicyText != nil {
		ko.Spec.LifecyclePolicy = getLifecyclePolicyResponse.LifecyclePolicyText
	}

	ko.Spec.Tags, err = rm.getRepositoryTags(ctx, string(*ko.Status.ACKResourceMetadata.ARN))
	rm.metrics.RecordAPICall("GET", "ListTagsForResource", err)
	if err != nil {
		return err
	}

	return nil
}

func (rm *resourceManager) getRepositoryTags(ctx context.Context, resourceARN string) ([]*v1alpha1.Tag, error) {
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
	tags := make([]*v1alpha1.Tag, 0, len(listTagsForResourceResponse.Tags))
	for _, tag := range listTagsForResourceResponse.Tags {
		tags = append(tags, &v1alpha1.Tag{
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
	a []*v1alpha1.Tag,
	b []*v1alpha1.Tag,
) bool {
	added, updated, removed := computeTagsDelta(a, b)
	return len(added) == 0 && len(updated) == 0 && len(removed) == 0
}

// computeTagsDelta compares two Tag arrays and return three different list
// containing the added, updated and removed tags.
// The removed tags only contains the Key of tags
func computeTagsDelta(
	a []*v1alpha1.Tag,
	b []*v1alpha1.Tag,
) (added, updated []*v1alpha1.Tag, removed []*string) {
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

// svcTagsFromResourceTags transforms a *v1alpha1.Tag array to a *svcsdk.Tag array.
func sdkTagsFromResourceTags(rTags []*v1alpha1.Tag) []*svcsdk.Tag {
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

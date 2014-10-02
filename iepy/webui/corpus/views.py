import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.shortcuts import get_object_or_404, render_to_response, redirect
from django.utils.decorators import method_decorator
from django.utils import formats

from extra_views import ModelFormSetView

from corpus.models import Relation, TextSegment, LabeledRelationEvidence, IEDocument
from corpus.forms import EvidenceForm, EvidenceOnDocumentForm, EvidenceToolboxForm


def next_segment_to_label(request, relation_id):
    relation = get_object_or_404(Relation, pk=relation_id)
    segment = relation.get_next_segment_to_label()
    if segment is None:
        return render_to_response('message.html',
                                  {'msg': 'There are no more evidence to label'})
    return redirect('corpus:label_evidence_for_segment', relation.pk, segment.pk)


def next_document_to_label(request, relation_id):
    relation = get_object_or_404(Relation, pk=relation_id)
    doc = relation.get_next_document_to_label()
    if doc is None:
        return render_to_response('message.html',
                                  {'msg': 'There are no more evidence to label'})
    return redirect('corpus:label_evidence_for_document', relation.pk, doc.pk)


def _navigate_labeled_items(request, relation_id, current_id, direction, type_):
    # The parameter current_id indicates where the user is situated when asking
    # to move back or forth
    type_name = 'document' if type_ == IEDocument else 'segment'
    url_name = 'corpus:label_evidence_for_%s' % type_name
    print(repr(url_name))
    relation = get_object_or_404(Relation, pk=relation_id)
    current = get_object_or_404(type_, pk=current_id)
    current_id = int(current_id)
    going_back = direction.lower() == 'back'
    obj_id_to_show = relation.labeled_neighbor(current, going_back)
    if obj_id_to_show is None:
        # Internal logic couldn't decide what other obj to show. Better to
        # forward to the one already shown
        response = redirect(url_name, relation.pk, current_id)
        messages.add_message(request, messages.WARNING,
                             'No other %s to show.' % type_name)
        return response
    else:
        response = redirect(url_name, relation.pk, obj_id_to_show)
        if obj_id_to_show == current_id:
            direction_str = "previous" if going_back else "next"
            messages.add_message(
                request, messages.WARNING,
                'No {0} {1} to show.'.format(direction_str, type_name))
        return response


def navigate_labeled_segments(request, relation_id, segment_id, direction):
    return _navigate_labeled_items(request, relation_id, segment_id,
                                   direction, TextSegment)


def navigate_labeled_documents(request, relation_id, document_id, direction):
    return _navigate_labeled_items(request, relation_id, document_id,
                                   direction, IEDocument)


class _BaseLabelEvidenceView(ModelFormSetView):
    form_class = EvidenceForm
    model = LabeledRelationEvidence
    extra = 0
    max_num = None
    can_order = False
    can_delete = False

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


class LabelEvidenceOnSegmentView(_BaseLabelEvidenceView):
    template_name = 'corpus/segment_questions.html'

    def get_context_data(self, **kwargs):
        ctx = super(LabelEvidenceOnSegmentView, self).get_context_data(**kwargs)
        self.segment.hydrate()
        title = "Labeling Evidence for Relation {0}".format(self.relation)
        subtitle = 'For Document "{0}", Text Segment id {1}'.format(
            self.segment.document.human_identifier,
            self.segment.id)

        ctx.update({
            'title': title,
            'subtitle': subtitle,
            'segment': self.segment,
            'segment_rich_tokens': list(self.segment.get_enriched_tokens()),
            'relation': self.relation
        })
        return ctx

    def get_segment_and_relation(self):
        if hasattr(self, 'segment') and hasattr(self, 'relation'):
            return self.segment, self.relation
        self.segment = get_object_or_404(TextSegment, pk=self.kwargs['segment_id'])
        self.segment.hydrate()
        self.relation = get_object_or_404(Relation, pk=self.kwargs['relation_id'])
        self.evidences = list(self.segment.get_labeled_evidences(self.relation))
        return self.segment, self.relation

    def get_queryset(self):
        segment, relation = self.get_segment_and_relation()
        return super().get_queryset().filter(
            segment=self.segment, relation=self.relation
        )

    def get_success_url(self):
        return reverse('corpus:next_segment_to_label', args=[self.relation.pk])

    def formset_valid(self, formset):
        """
        Add message to the user, and set who made this labeling (judge).
        """
        for form in formset:
            if form.has_changed():
                form.instance.judge = str(self.request.user)
        result = super().formset_valid(formset)
        messages.add_message(self.request, messages.INFO,
                             'Changes saved for segment {0}.'.format(self.segment.id))
        return result


class LabelEvidenceOnDocumentView(_BaseLabelEvidenceView):
    template_name = 'corpus/document_questions.html'
    form_class = EvidenceOnDocumentForm

    def get_text_segments(self, only_with_evidences=False):
        if only_with_evidences:
            return self.relation._matching_text_segments().filter(
                document_id=self.document.id).order_by('offset').distinct()
        else:
            return self.document.get_text_segments()

    def get_context_data(self, **kwargs):
        ctx = super(LabelEvidenceOnDocumentView, self).get_context_data(**kwargs)
        title = "Labeling Evidence for Relation {0}".format(self.relation)
        subtitle = 'For Document "{0}"'.format(self.document.human_identifier)

        segments_with_rich_tokens = []
        for segment in self.get_text_segments(only_with_evidences=True):
            segment.hydrate()
            segments_with_rich_tokens.append(
                {'id': segment.id,
                 'rich_tokens': list(segment.get_enriched_tokens())}
            )

        if not segments_with_rich_tokens:
            ctx = {
                'title': title,
                'document': self.document,
                'relation': self.relation,
            }
            return ctx

        forms_values = {}
        eos_propperties = {}
        relations_list = []
        formset = ctx['formset']
        for form_idx, form in enumerate(formset):
            evidence = form.instance

            left_eo_id = evidence.left_entity_occurrence.pk
            right_eo_id = evidence.right_entity_occurrence.pk
            info = "Labeled as {} by {} on {}".format(
                evidence.label,
                evidence.judge if evidence.judge else "unknown",
                formats.date_format(
                    evidence.modification_date, "SHORT_DATETIME_FORMAT"
                )
            )
            relations_list.append({
                "relation": [left_eo_id, right_eo_id],
                "form_id": form.prefix,
                "info": info,
            })

            forms_values[form.prefix] = evidence.label;

            for eo_id in [left_eo_id, right_eo_id]:
                if eo_id not in eos_propperties:
                    eos_propperties[eo_id] = {
                        'selectable': True,
                        'selected': False,
                    }

        form_toolbox = EvidenceToolboxForm(prefix='toolbox')
        question_options = [x[0] for x in form_toolbox.fields["label"].choices]

        ctx.update({
            'title': title,
            'subtitle': subtitle,
            'document': self.document,
            'segments': segments_with_rich_tokens,
            'relation': self.relation,
            'form_for_others': EvidenceForm(prefix='for_others'),
            'form_toolbox': form_toolbox,
            'initial_tool': LabeledRelationEvidence.YESRELATION,
            'eos_propperties': json.dumps(eos_propperties),
            'relations_list': json.dumps(relations_list),
            'forms_values': json.dumps(forms_values),
            'question_options': question_options,
        })
        return ctx

    def get_document_and_relation(self):
        if hasattr(self, 'document') and hasattr(self, 'relation'):
            return self.document, self.relation
        self.document = get_object_or_404(IEDocument, pk=self.kwargs['document_id'])
        self.relation = get_object_or_404(Relation, pk=self.kwargs['relation_id'])
        self.evidences = []
        for segment in self.document.get_text_segments():
            self.evidences.extend(
                list(segment.get_labeled_evidences(self.relation))
            )
        return self.document, self.relation

    def get_queryset(self):
        document, relation = self.get_document_and_relation()
        return super().get_queryset().filter(
            segment__document_id=document.id, relation=self.relation
        )

    def get_success_url(self):
        if self.is_partial_save():
            return self.request.META.get('HTTP_REFERER')
        return reverse('corpus:next_document_to_label', args=[self.relation.pk])

    def get_default_label_value(self):
        return self.request.POST.get('for_others-label', None)

    def is_partial_save(self):
        # "partial saves" is a hack to allow edition of the Preprocess while labeling
        return self.request.POST.get('partial_save', '') == 'enabled'

    def formset_valid(self, formset):
        """
        Add message to the user, handle the "for the rest" case, and set
        who made this labeling (judge).
        """
        partial = self.is_partial_save()
        if partial:
            default_lbl = None
        else:
            default_lbl = self.get_default_label_value()
        for form in formset:
            if form.instance.label is None:
                form.instance.label = default_lbl
            if form.has_changed():
                form.instance.judge = str(self.request.user)
        result = super().formset_valid(formset)
        if not partial:
            messages.add_message(
                self.request, messages.INFO,
                'Changes saved for document {0}.'.format(self.document.id)
            )
        return result

    def get_formset_kwargs(self):
        """
        If is a partial save, hackes the forms to match the queryset so it
        matches the ones that actually has a LabeledRelationEvidence.
        This is to handle the case where an entity occurrence was removed.
        """

        kwargs = super().get_formset_kwargs()
        queryset = kwargs.get("queryset", [])
        data = kwargs.get("data", {})
        partial = data.get("partial_save")

        if partial != "enabled":
            return kwargs

        new_data = data.copy()

        initial_forms_key = "form-INITIAL_FORMS"
        total_forms_key = "form-TOTAL_FORMS"
        query_ids = [str(x.id) for x in queryset]
        included_forms = []
        for key, value in data.items():
            if key.endswith("-id"):
                form_id = key[:-3]
                label_key = "{}-label".format(form_id)

                if value in query_ids:
                    label = data[label_key]
                    included_forms.append((value, label))

                new_data.pop(key)
                new_data.pop(label_key)

        for i, (form_id, label) in enumerate(included_forms):
            form_id_key = "form-{}-id".format(i)
            form_label_key = "form-{}-label".format(i)
            new_data[form_id_key] = form_id
            new_data[form_label_key] = label

        new_data[total_forms_key] = str(len(included_forms))
        new_data[initial_forms_key] = str(len(included_forms))

        kwargs["data"] = new_data
        return kwargs

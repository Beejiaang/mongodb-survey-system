"""Phase two end-to-end: reuse, version, share, bank, respond and aggregate."""
from bson import ObjectId

from stage2_support import StageTwoFixture
from jumps import question_revision
from question_library import bank_revision, get_bank, get_item


class PhaseTwoAcceptanceTests(StageTwoFixture):
    def test_complete_version_bank_shared_response_and_statistics_flow(self):
        created=self.post('/library/new',kind='number',input_type='number',title='年龄',
                          min_value='0',max_value='120',integer_only='on',required='on')
        self.assertEqual(created.status_code,303)
        item=self.database.question_items.find_one({'owner_id':self.owner})
        iid,v1=item['_id'],item['current_version_id']
        manage=self.post('/surveys/new',title='A使用旧版本').location
        sid=ObjectId(manage.rsplit('/',1)[1])
        bank_response=self.post('/library/banks',name='常用年龄题库')
        bid=ObjectId(bank_response.location.rsplit('/',1)[1])
        self.assertEqual(self.post(bank_response.location,action='put',version_id=str(v1),
                                  revision=bank_revision(get_bank(self.database,bid,self.owner))).status_code,303)
        picker=f'/library/select/{sid}?bank={bid}'
        self.assertIn(str(v1),self.client.get(picker).get_data(as_text=True))
        self.assertEqual(self.post(picker,version_id=str(v1)).status_code,303)
        self.assertEqual(self.post(manage+'/publish').status_code,303)
        frozen=self.database.surveys.find_one({'_id':sid})
        self.assertEqual(self.post(f'/library/items/{iid}/versions/new?base={v1}',kind='number',
                                  input_type='number',title='年龄',min_value='0',max_value='100',
                                  integer_only='on',required='on',change_note='上限改100').status_code,303)
        v2=get_item(self.database,iid,self.owner)['current_version_id']
        self.assertNotEqual(v1,v2)
        b_manage=self.post('/surveys/new',title='B使用新版本').location
        bid_survey=ObjectId(b_manage.rsplit('/',1)[1])
        self.assertEqual(self.post(f'/library/versions/{v2}/use',survey_id=str(bid_survey)).status_code,303)
        self.assertEqual(self.post(b_manage+'/publish').status_code,303)
        self.assertEqual(self.database.surveys.find_one({'_id':sid}),frozen)
        self.assertEqual(self.post(f'/library/items/{iid}/share',action='share',username='other').status_code,303)
        self.login(self.other)
        self.assertEqual(self.client.get(f'/library/items/{iid}').status_code,200)
        own=self.post('/surveys/new',title='共享接收者问卷').location
        other_sid=ObjectId(own.rsplit('/',1)[1])
        self.assertEqual(self.post(f'/library/versions/{v1}/use',survey_id=str(other_sid)).status_code,303)
        self.assertEqual(self.post(own+'/publish').status_code,303)
        self.assertEqual(self.post(f'/library/versions/{v1}/restore').status_code,404)
        self.login(self.third)
        for target,value in [(sid,'120'),(bid_survey,'100'),(other_sid,'42')]:
            path=self.post(f'/survey/{target}/start').location
            self.assertTrue(path)
            self.assertEqual(self.post(path,action='next',version='0',answer=value).status_code,303)
            self.assertEqual(self.post(path,action='submit',version='1').status_code,303)
            self.assertIn('提交成功',self.client.get(path).get_data(as_text=True))
        self.login(self.owner)
        old_stats=self.client.get(f'/library/versions/{v1}/results').get_data(as_text=True)
        new_stats=self.client.get(f'/library/versions/{v2}/results').get_data(as_text=True)
        self.assertIn('120.00',old_stats)
        self.assertNotIn('42.00',old_stats)
        self.assertIn('100.00',new_stats)
        restored=self.post(f'/library/versions/{v1}/restore')
        self.assertEqual(restored.status_code,303)
        current=get_item(self.database,iid,self.owner)
        self.assertEqual(current['current_version_number'],3)
        history=self.client.get(restored.location).get_data(as_text=True)
        self.assertIn('恢复自v1',history)
        self.assertEqual(get_bank(self.database,bid,self.owner)['entries'][0]['version_id'],v1)
        # Submission admission intentionally updates survey.updated_at in phase one.
        after=self.database.surveys.find_one({'_id':sid})
        self.assertEqual({k:v for k,v in after.items() if k!='updated_at'},
                         {k:v for k,v in frozen.items() if k!='updated_at'})
